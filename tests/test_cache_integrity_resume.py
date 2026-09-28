import io
import json
import hashlib
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from runtime.drive_cache import DriveCache, DriveFileSpec
from runtime.runtime_variants import RuntimeVariants, PROFILES

class Response:
    def __init__(self, body, status=200, headers=None):
        self.body=io.BytesIO(body);self.status=status;self.headers=headers or {}
    def read(self, size=-1):return self.body.read(size)
    def __enter__(self):return self
    def __exit__(self,*a):pass

class CacheIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.cache=DriveCache(self.root)
        self.body=b'GGUFabcdefgh01234567'
        self.spec=DriveFileSpec('validDriveFile0123','model.gguf',len(self.body),hashlib.md5(self.body).hexdigest(),modified_time='v1')
    def tearDown(self):self.tmp.cleanup()
    def download(self,body=None,status=200,headers=None,progress=None):
        with patch('runtime.drive_cache.urlopen',return_value=Response(self.body if body is None else body,status,headers)) as call:
            result=self.cache.download(self.spec,'VERY_PRIVATE_TOKEN',progress)
        return result,call
    def test_stream_checksums_are_recorded(self):
        path,_=self.download();record=self.cache.metadata(self.spec)
        self.assertEqual(record['verified_md5'],self.spec.md5_checksum)
        self.assertEqual(record['sha256'],hashlib.sha256(self.body).hexdigest())
        self.assertNotIn('VERY_PRIVATE_TOKEN',json.dumps(record))
    def test_wrong_md5_never_promoted(self):
        with self.assertRaises(ValueError):self.download(b'x'*len(self.body))
        self.assertIsNone(self.cache.cached_path(self.spec));self.assertTrue(list(self.root.glob('*.bad-checksum-*')))
    def test_truncated_response_never_complete(self):
        with self.assertRaises((ValueError,RuntimeError)):self.download(self.body[:-1])
        self.assertIsNone(self.cache.cached_path(self.spec))
    def test_response_too_long_rejected(self):
        with self.assertRaises(ValueError):self.download(self.body+b'x')
        self.assertIsNone(self.cache.cached_path(self.spec))
    def interrupt(self):
        # Serve a bounded prefix; real .part identity metadata is written by production download.
        with self.assertRaises((ValueError,RuntimeError)):self.download(self.body[:8])
        return self.cache._paths(self.spec)[1]
    def test_resume_valid_range_appends_then_hashes(self):
        part=self.interrupt();self.assertEqual(part.stat().st_size,8)
        path,call=self.download(self.body[8:],206,{'Content-Range':f'bytes 8-{len(self.body)-1}/{len(self.body)}'})
        self.assertEqual(path.read_bytes(),self.body)
        self.assertEqual(call.call_args.args[0].get_header('Range'),'bytes=8-')
    def test_200_during_resume_restarts_instead_of_appending(self):
        self.interrupt();path,_=self.download();self.assertEqual(path.read_bytes(),self.body)
    def test_wrong_range_is_not_written(self):
        part=self.interrupt()
        with self.assertRaises(ValueError):self.download(self.body[8:],206,{'Content-Range':'bytes 1-19/20'})
        self.assertEqual(part.stat().st_size,8)
    def test_changed_version_discards_old_partial_safely(self):
        self.interrupt();self.spec=replace(self.spec,modified_time='v2')
        path,call=self.download();self.assertIsNone(call.call_args.args[0].get_header('Range'))
        self.assertTrue(list(self.root.glob('*.stale-*')));self.assertEqual(path.read_bytes(),self.body)
    def test_tampered_file_invalidates_cached_record(self):
        path,_=self.download();path.write_bytes(b'x'*len(self.body))
        self.assertIsNone(self.cache.cached_path(self.spec))
    def test_legacy_same_bytes_reverified_without_download(self):
        final,_,meta=self.cache._paths(self.spec);final.write_bytes(self.body)
        meta.write_text(json.dumps({'file_id':self.spec.file_id,'size':self.spec.size}))
        with patch('runtime.drive_cache.urlopen') as call:result=self.cache.download(self.spec,'token')
        call.assert_not_called();self.assertEqual(result,final);self.assertIsNotNone(self.cache.metadata(self.spec))
    def test_warm_cache_new_object_no_network(self):
        path,_=self.download();other=DriveCache(self.root)
        with patch('runtime.drive_cache.urlopen') as call:self.assertEqual(other.download(self.spec,'newtoken'),path)
        call.assert_not_called()
    def test_checksumless_partial_not_resumed(self):
        self.spec=replace(self.spec,md5_checksum=None);self.interrupt()
        _,call=self.download();self.assertIsNone(call.call_args.args[0].get_header('Range'))
    def test_symlink_target_rejected(self):
        outside=self.root/'outside';outside.write_bytes(self.body)
        final,_,_=self.cache._paths(self.spec);final.symlink_to(outside)
        with self.assertRaises(ValueError):self.download()
    def test_token_required_on_cold_cache(self):
        with self.assertRaises((ValueError,PermissionError)):self.cache.download(self.spec,'')
    def test_checksum_format_rejected(self):
        with self.assertRaises(ValueError):DriveFileSpec.from_payload({'drive_file_id':'abcdefgh','file_name':'a','size':2,'md5_checksum':'abc'})

class VariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.variants=RuntimeVariants(self.root);self.key='qwen2511_gguf_q4km'
        self.files=[]
        for role,repo,name in PROFILES[self.key]['files']:
            data=(role+'test').encode();self.files.append(dict(role=role,repo=repo,filename=name,name=name.rsplit('/',1)[-1],revision='a'*40,sha256=hashlib.sha256(data).hexdigest(),size=len(data)))
        self.manifest=dict(profile=self.key,schema_version=1,files=self.files,total_bytes=sum(f['size'] for f in self.files))
        folder=self.variants.root/self.key;folder.mkdir()
        (folder/'manifest.json').write_text(json.dumps(self.manifest));self.folder=folder
    def tearDown(self):self.tmp.cleanup()
    def download(self):
        def response(req,timeout):
            item=next(f for f in self.files if f['name'] in req.full_url)
            return Response((item['role']+'test').encode())
        with patch('runtime.runtime_variants.urlopen',side_effect=response) as call:
            result=self.variants.prepare(self.key,threading.Event(),lambda _:None)
        return result,call
    def test_manifest_forbids_arbitrary_repository(self):
        data=json.loads(json.dumps(self.manifest));data['files'][0]['repo']='evil/model'
        with self.assertRaises(ValueError):self.variants.validate_manifest(self.key,data)
    def test_manifest_requires_immutable_revision_and_hash(self):
        for key,value in [('revision','main'),('sha256','x'),('size',0)]:
            data=json.loads(json.dumps(self.manifest));data['files'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.variants.validate_manifest(self.key,data)
    def test_download_verified_and_cached(self):
        files,call=self.download();self.assertEqual(call.call_count,3)
        for k,p in files.items():self.assertEqual(p.read_bytes(),(k+'test').encode())
        with patch('runtime.runtime_variants.urlopen') as call:self.variants.prepare(self.key,threading.Event(),lambda _:None)
        call.assert_not_called()
    def test_existing_invalid_hash_blocks(self):
        (self.folder/self.files[0]['name']).write_bytes(b'x'*self.files[0]['size'])
        with self.assertRaises(ValueError):self.download()
    def test_warm_symlink_cannot_bypass_hash(self):
        files,_=self.download();target=files['unet'];outside=self.root/'outside';outside.write_bytes(target.read_bytes())
        target.unlink();target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'Symlink'):self.download()
    def test_cancel_does_not_download(self):
        cancel=threading.Event();cancel.set()
        with patch('runtime.runtime_variants.urlopen') as call,self.assertRaises(InterruptedError):self.variants.prepare(self.key,cancel,lambda _:None)
        call.assert_not_called()
    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):self.variants.prepare('anything',threading.Event(),lambda _:None)
