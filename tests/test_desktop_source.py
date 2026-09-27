"""Desktop filesystem fixture tests, not Google DriveFS or GPU acceptance."""
import hashlib
import http.client
import json
import os
import stat
import struct
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer

from runtime.desktop_source import DesktopSource, DesktopAwareCache, PREFIX, CHUNK, is_link, safe_parts
from runtime.drive_cache import DriveFileSpec
from runtime import application


class DesktopFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / 'Drive 同步' / 'AI-Model-Vault'; self.vault.mkdir(parents=True)
        self.model = self.vault / 'llm' / 'Qwen 小模型' / 'test.gguf'; self.model.parent.mkdir(parents=True)
        self.model.write_bytes(struct.pack('<4sIQQ', b'GGUF', 3, 1, 1) + b'test' * 100)
        self.source = DesktopSource(base / 'cache', base / 'settings/desktop.json')
        self.cache = DesktopAwareCache(base / 'cache', self.source)
        self.source.configure(str(self.vault))
        self.scan()

    def scan(self):
        snapshot, files = self.source.scan(self.vault)
        self.source.files, self.source.snapshot_data = files, snapshot
        self.source.state['phase'] = 'complete'
        key, item = next((k, v) for k, v in files.items() if v.relative.endswith('test.gguf'))
        self.spec = DriveFileSpec(key, 'test.gguf', item.version[0])
        return snapshot

    def tearDown(self):
        if self.source.thread: self.source.thread.join(2)
        self.tmp.cleanup()

    def test_metadata_scan_does_not_open_weights(self):
        with patch.object(Path, 'open', side_effect=AssertionError('weight should not be opened')):
            result, _ = self.source.scan(self.vault)
        self.assertEqual(result['files'], 1)

    def test_non_ascii_directories(self):
        self.assertIn('Qwen 小模型', self.source.files[self.spec.file_id].relative)

    def test_no_absolute_source_paths_in_snapshot(self):
        self.assertNotIn(str(self.vault), json.dumps(self.source.snapshot_data))

    def test_current_readonly_handle(self):
        self.assertTrue(self.spec.file_id.startswith(PREFIX))
        self.assertEqual(self.source.resolve(self.spec).checked_path(), self.model)

    def test_scanned_id_stable_on_rescan(self):
        first = self.spec.file_id; self.scan(); self.assertEqual(first, self.spec.file_id)

    def test_missing_registry_is_explicit(self):
        self.assertIsNone(self.source.snapshot_data['registry'])
        self.assertTrue(self.source.snapshot_data['warnings'])

    def test_read_bounded_registry(self):
        registry = {'schema_version':'1', 'models':[]}
        (self.vault/'model_metadata.json').write_text(json.dumps(registry))
        self.assertEqual(self.scan()['registry'], registry)

    def test_bad_registry_fails(self):
        (self.vault/'model_metadata.json').write_text('not json')
        with self.assertRaises(ValueError): self.scan()

    def test_ignore_partials_and_hidden_cache(self):
        (self.model.parent/'bad.gguf.part').write_bytes(b'data')
        (self.vault/'.cache').mkdir(); (self.vault/'.cache'/'leak.gguf').write_bytes(b'data')
        self.assertEqual(self.scan()['files'], 1)

    def test_empty_files_excluded(self):
        (self.model.parent/'empty.gguf').touch()
        snapshot = self.scan(); self.assertEqual(snapshot['files'],1)
        self.assertIn('空文件', '\n'.join(snapshot['warnings']))

    def test_symlink_skipped(self):
        outside = Path(self.tmp.name)/'private.gguf'; outside.write_bytes(b'private')
        try: (self.model.parent/'shortcut.gguf').symlink_to(outside)
        except OSError: self.skipTest('OS does not permit test symlinks')
        self.assertEqual(self.scan()['files'],1)

    def test_folder_limit_not_partial_success(self):
        with self.assertRaises(ValueError): self.source.scan(self.vault,max_folders=1)

    def test_file_limit_not_partial_success(self):
        with self.assertRaises(ValueError): self.source.scan(self.vault,max_files=0)

    def test_cancel_scan(self):
        self.source.cancel.set()
        with self.assertRaises(InterruptedError): self.scan()

    def test_root_cache_overlap_refused(self):
        with self.assertRaises(ValueError): self.source.configure(str(self.cache.root))
        with self.assertRaises(ValueError): self.source.configure(str(Path(self.tmp.name)))

    def test_relative_root_refused(self):
        with self.assertRaises(ValueError): self.source.configure('relative/folder')

    def test_unknown_id_refused(self):
        with self.assertRaises(ValueError): self.source.resolve(replace(self.spec,file_id=PREFIX+'a'*64))

    def test_forged_filename_refused(self):
        with self.assertRaises(ValueError): self.source.resolve(replace(self.spec,name='../secret.gguf'))

    def test_forged_size_refused(self):
        with self.assertRaises(ValueError): self.source.resolve(replace(self.spec,size=1))

    def test_changed_source_refused(self):
        self.model.write_bytes(b'new version')
        with self.assertRaises(ValueError): self.cache.download(self.spec,'')

    def test_deleted_source_refused(self):
        self.model.unlink()
        with self.assertRaises(OSError): self.cache.download(self.spec,'')

    def test_desktop_skips_oauth(self):
        provider=Mock(side_effect=AssertionError('OAuth must not run'))
        payload={'drive_file_id':self.spec.file_id,'file_name':self.spec.name,'size':self.spec.size}
        self.assertEqual(self.cache.access_token(payload,provider),'')
        provider.assert_not_called()

    def test_api_retains_oauth(self):
        provider=Mock(return_value='real-cloud-token')
        self.assertEqual(self.cache.access_token({'drive_file_id':'cloudFile123','file_name':'test.gguf','size':5},provider),'real-cloud-token')
        provider.assert_called_once()

    def test_mixed_sources_refused(self):
        payload={'files':[{'id':self.spec.file_id,'name':self.spec.name,'size':self.spec.size},{'id':'cloudFile123','name':'x.json','size':10}]}
        with self.assertRaises(ValueError): self.cache.access_token(payload,Mock())

    def test_copy_never_calls_drive_api_or_stores_token(self):
        original=self.model.read_bytes()
        with patch('runtime.drive_cache.urlopen',side_effect=AssertionError('no API request')):
            path=self.cache.download(self.spec,'SECRET-MUST-NOT-PERSIST')
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(self.model.read_bytes(),original)
        meta=self.cache.metadata(self.spec)
        self.assertEqual(meta['sha256'],hashlib.sha256(original).hexdigest())
        self.assertNotIn('SECRET-MUST-NOT-PERSIST',json.dumps(meta))
        self.assertTrue(meta['read_only_source'])

    def test_copy_reuses_completed_cache(self):
        first=self.cache.download(self.spec,'')
        with patch('runtime.desktop_source.os.open',side_effect=AssertionError('do not re-read source bytes')):
            second=self.cache.download(self.spec,'')
        self.assertEqual(first,second)

    def test_delete_cache_keeps_source(self):
        original=self.model.read_bytes();self.cache.download(self.spec,'')
        self.cache.delete_all()
        self.assertEqual(self.model.read_bytes(),original)
        self.assertFalse(self.cache.cached_path(self.spec))

    def test_source_change_creates_new_cache_identity(self):
        old=self.spec.file_id;self.cache.download(self.spec,'')
        self.model.write_bytes(self.model.read_bytes()+b'changed');self.scan()
        self.assertNotEqual(old,self.spec.file_id)

    def test_cancelled_copy_keeps_part_and_resumes(self):
        self.model.write_bytes(b'a'*(CHUNK+100));self.scan()
        def stop(received,total):
            if received>=CHUNK:raise InterruptedError('cancel fixture')
        with self.assertRaises(InterruptedError):self.cache.download(self.spec,'',stop)
        final,partial,_=self.cache._paths(self.spec)
        self.assertFalse(final.exists());self.assertEqual(partial.stat().st_size,CHUNK)
        path=self.cache.download(self.spec,'')
        self.assertEqual(path.read_bytes(),self.model.read_bytes())
        self.assertFalse(partial.exists())

    def test_bad_partial_is_not_appended(self):
        _,partial,_=self.cache._paths(self.spec);partial.write_bytes(b'x'*200)
        self.assertEqual(self.cache.download(self.spec,'').read_bytes(),self.model.read_bytes())

    def test_size_mismatch_after_read_never_commits(self):
        def mutate(received,total):
            if received==total:self.model.write_bytes(b'new data')
        with self.assertRaises(ValueError):self.cache.download(self.spec,'',mutate)
        self.assertFalse(self.cache._paths(self.spec)[0].exists())

    def test_no_space_blocks_before_open(self):
        with patch.object(self.cache,'download_preflight',return_value={'ok':False}),patch('runtime.desktop_source.os.open') as opened:
            with self.assertRaises(RuntimeError):self.cache.download(self.spec,'')
            opened.assert_not_called()

    def test_cloud_reparse_flag_not_symlink(self):
        info=Mock(st_mode=stat.S_IFREG,st_reparse_tag=0x9000001A)
        self.assertFalse(is_link(self.model,info))

    def test_windows_junction_refused(self):
        info=Mock(st_mode=stat.S_IFDIR,st_reparse_tag=0xA0000003)
        self.assertTrue(is_link(self.model,info))

    def test_path_validation(self):
        for path in ['../x','a/../x','a\\x','/etc/password','C:/secret','a./test.gguf']:
            with self.subTest(path=path),self.assertRaises(ValueError):safe_parts(path)


class DesktopHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),application.ApplicationHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=3)
        conn.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers=headers or {})
        response=conn.getresponse();data=response.read();status=response.status;conn.close();return status,json.loads(data)
    def test_new_version(self):self.assertEqual(self.request('/health')[1]['version'],18)
    def test_desktop_requires_remote_token(self):
        with patch.object(application.bridge,'REMOTE_TOKEN','fixture'):
            self.assertEqual(self.request('/v1/desktop/status')[0],401)
            self.assertEqual(self.request('/v1/desktop/scan',{})[0],401)
    def test_paths_not_configurable_from_http(self):
        self.assertEqual(self.request('/v1/desktop/pick',{'root':'C:/Windows'})[0],400)
    def test_native_picker_only_local_frontend(self):
        with patch.object(application.DESKTOP,'pick') as pick:
            self.assertEqual(self.request('/v1/desktop/pick',{},headers={'Origin':'https://jvust.github.io'})[0],403)
            pick.assert_not_called()
    def test_picker_rejects_missing_origin(self):
        self.assertEqual(self.request('/v1/desktop/pick',{})[0],403)
    def test_probe_cannot_take_path(self):
        self.assertEqual(self.request('/v1/desktop/probe',{'path':'/etc/passwd'})[0],400)
    def test_scan_without_config_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(application.DESKTOP,'config_path',Path(temp)/'none.json'):
            self.assertEqual(self.request('/v1/desktop/scan',{})[0],400)
    def test_probe_rejects_cloud_id(self):
        self.assertEqual(self.request('/v1/desktop/probe',{'drive_file_id':'realCloud123','file_name':'x.gguf','size':5})[0],400)


if __name__=='__main__':unittest.main()
