"""Release contract tests. Mocked inference is explicitly NOT real model acceptance."""
import base64
import http.client
import json
import os
import struct
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('MODEL_CACHE_ROOT', tempfile.mkdtemp(prefix='model-tests-'))
from runtime.native_worker import validate_input, series_values, integer, image_bytes, CATALOG
from runtime.native_runtime import NativeRuntime, validate_manifest, verify_package
from runtime.managed_env import extract_safe, run_checked
from runtime.application import ApplicationHandler, byte_range
from runtime import application
from runtime.drive_cache import DriveCache
from http.server import ThreadingHTTPServer


def manifest(model_id='chronos_2', paths=None):
    paths = paths or ['config.json', 'model.safetensors']
    return {'model_id': model_id, 'package_path': 'timeseries/test', 'manifest_files': [{'drive_file_id': 'testFileNumber' + str(i), 'file_name': Path(path).name, 'relative_path': 'timeseries/test/' + path, 'size': 100} for i, path in enumerate(paths)]}


class InputContract(unittest.TestCase):
    def test_all_five_families_declared(self): self.assertEqual(len(CATALOG), 5)
    def test_csv_column(self): self.assertEqual(series_values({'csv': 'y,z\n' + '\n'.join(f'{i},0' for i in range(16)), 'column': 'y'}), list(range(16)))
    def test_numeric_text(self): self.assertEqual(len(series_values({'csv': ' '.join(str(i) for i in range(16))})), 16)
    def test_missing_csv_column(self):
        with self.assertRaises(ValueError): series_values({'csv': 'a,b\n1,2', 'column': 'c'})
    def test_nan_rejected(self):
        with self.assertRaises(ValueError): series_values({'values': [1]*15 + [float('nan')]})
    def test_infinity_rejected(self):
        with self.assertRaises(ValueError): series_values({'values': [1]*15 + [float('inf')]})
    def test_missing_values_rejected(self):
        with self.assertRaises(ValueError): series_values({'values': [1]*15 + [None]})
    def test_boolean_value_rejected(self):
        with self.assertRaises(ValueError): series_values({'values': [1]*15 + [False]})
    def test_no_silent_truncation(self):
        with self.assertRaises(ValueError): series_values({'values': [1]*2049})
    def test_short_series_rejected(self):
        with self.assertRaises(ValueError): series_values({'values': [1]*15})
    def test_valid_forecast(self): self.assertEqual(validate_input('chronos_2', {'values': [1]*16})['horizon'], 24)
    def test_fractional_horizon(self):
        with self.assertRaises(ValueError): validate_input('chronos_2', {'values': [1]*16, 'horizon': 1.5})
    def test_bad_frequency(self):
        with self.assertRaises(ValueError): validate_input('timesfm_2_0_500m', {'values': [1]*16, 'frequency': 3})
    def test_unknown_family(self):
        with self.assertRaises(ValueError): validate_input('arbitrary-code', {})
    def test_bad_base64(self):
        with self.assertRaises(ValueError): image_bytes('not-base64!')
    def test_no_remote_image_url(self):
        with self.assertRaises(ValueError): image_bytes('http://127.0.0.1:1234')
    def test_data_uri(self): self.assertEqual(image_bytes('data:image/png;base64,' + base64.b64encode(b'abc').decode()), b'abc')
    def test_video_dimensions(self):
        with self.assertRaises(ValueError): validate_input('wan22_t2v_a14b', {'prompt': 'test', 'width': 833})
    def test_video_frames(self):
        with self.assertRaises(ValueError): validate_input('wan22_t2v_a14b', {'prompt': 'test', 'frames': 48})
    def test_video_i2v_needs_image(self):
        with self.assertRaises(ValueError): validate_input('wan22_i2v_a14b', {'prompt': 'test'})
    def test_video_default_frames(self): self.assertEqual(validate_input('wan22_t2v_a14b', {'prompt': 'test'})['frames'], 49)


class ManifestContract(unittest.TestCase):
    def test_basic_chronos(self): self.assertEqual(len(validate_manifest('chronos_2', manifest())[1]), 2)
    def test_weights_missing(self):
        with self.assertRaises(ValueError): validate_manifest('chronos_2', manifest(paths=['config.json']))
    def test_config_missing(self):
        with self.assertRaises(ValueError): validate_manifest('chronos_2', manifest(paths=['model.safetensors']))
    def test_ocr_processor_missing(self):
        with self.assertRaises(ValueError): validate_manifest('got_ocr2', manifest())
    def test_timesfm_checkpoint(self): self.assertEqual(len(validate_manifest('timesfm_2_0_500m', manifest(paths=['torch_model.ckpt']))[1]), 1)
    def test_wrong_timesfm_checkpoint(self):
        with self.assertRaises(ValueError): validate_manifest('timesfm_2_0_500m', manifest())
    def test_legacy_remote_code_rejected(self):
        with self.assertRaises(ValueError): validate_manifest('chronos_2', manifest(paths=['config.json','model.safetensors','modeling.py']))
    def test_windows_duplicate(self):
        with self.assertRaises(ValueError): validate_manifest('chronos_2', manifest(paths=['config.json','model.safetensors','CONFIG.json']))
    def test_traversal(self):
        with self.assertRaises(ValueError): validate_manifest('chronos_2', manifest(paths=['../../escape']))
    def test_shard_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'x':'missing.safetensors'}}))
            with self.assertRaises(ValueError): verify_package('chronos_2', root)
    def test_legacy_ocr_config_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'config.json').write_text('{"model_type":"legacy-got"}')
            with self.assertRaises(ValueError): verify_package('got_ocr2', root)
    def test_exact_native_ocr_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'config.json').write_text('{"model_type":"got_ocr2"}')
            verify_package('got_ocr2', root)
    def test_zip_slip(self):
        with tempfile.TemporaryDirectory() as temp:
            archive=Path(temp)/'bad.zip'
            with zipfile.ZipFile(archive,'w') as z: z.writestr('../escape','bad')
            with self.assertRaises(ValueError): extract_safe(archive, Path(temp)/'out')


class JobContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.runtime=NativeRuntime(DriveCache(Path(self.temp.name)), lambda:'DO-NOT-PERSIST-TOKEN')
    def tearDown(self): self.temp.cleanup()
    def test_idle_stop_is_noop(self): self.assertEqual(self.runtime.stop()['phase'],'idle')
    def test_missing_files_never_start_download(self):
        with self.assertRaises(ValueError): self.runtime.start({'model_id':'chronos_2','input':{'values':[1]*16},'package_path':'a'})
        self.assertIsNone(self.runtime.thread)
    def test_input_before_download(self):
        with self.assertRaises(ValueError): self.runtime.start({**manifest(), 'input':{'values':[1]}})
        self.assertIsNone(self.runtime.thread)
    def test_result_path_rejects_traversal(self):
        with self.assertRaises(ValueError): self.runtime.result_path('../secret')
    def test_missing_result(self):
        with self.assertRaises(FileNotFoundError): self.runtime.result_path('0'*32)
    def test_cache_identity_includes_drive_account_assets(self):
        a=validate_manifest('chronos_2',manifest())[1]; b=validate_manifest('chronos_2',manifest())[1]
        from dataclasses import replace
        b[0]['spec']=replace(b[0]['spec'],file_id='differentDriveAccountFile')
        self.assertNotEqual(self.runtime._materialize('chronos_2',a),self.runtime._materialize('chronos_2',b))
    def test_low_memory_blocks(self):
        with patch('runtime.native_runtime.system_memory_status',return_value={'available_bytes':1}):
            result=self.runtime.plan(manifest())
        self.assertFalse(result['ready']); self.assertIn('RAM',';'.join(result['reasons']))
    def test_unsupported_gpu_blocks(self):
        with patch('runtime.native_runtime.nvidia_status', return_value={'detected':False}):
            result=self.runtime.plan({**manifest(),'model_id':'wan22_t2v_a14b'})
        self.assertFalse(result['ready']); self.assertTrue(any('NVIDIA' in reason for reason in result['reasons']))
    def test_no_credential_in_snapshot(self): self.assertNotIn('DO-NOT-PERSIST', json.dumps(self.runtime.snapshot()))


class RangeContract(unittest.TestCase):
    def test_full(self): self.assertEqual(byte_range(None,10),(0,9,200))
    def test_bounded(self): self.assertEqual(byte_range('bytes=2-5',10),(2,5,206))
    def test_open_end(self): self.assertEqual(byte_range('bytes=2-',10),(2,9,206))
    def test_suffix(self): self.assertEqual(byte_range('bytes=-3',10),(7,9,206))
    def test_clamp(self): self.assertEqual(byte_range('bytes=2-999',10),(2,9,206))
    def test_reverse_rejected(self):
        with self.assertRaises(ValueError): byte_range('bytes=5-2',10)
    def test_multi_rejected(self):
        with self.assertRaises(ValueError): byte_range('bytes=1-2,4-5',10)
    def test_zero_suffix_rejected(self):
        with self.assertRaises(ValueError): byte_range('bytes=-0',10)
    def test_outside_rejected(self):
        with self.assertRaises(ValueError): byte_range('bytes=10-',10)
    def test_empty_range(self):
        with self.assertRaises(ValueError): byte_range('bytes=0-',0)


class HTTPContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),ApplicationHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def req(self,path,headers=None,method='GET',body=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=3)
        conn.request(method,path,body=body,headers=headers or {}); response=conn.getresponse(); data=response.read(); conn.close(); return response.status,data
    def test_health(self): self.assertEqual(self.req('/health')[0],200)
    def test_native_catalog(self): self.assertEqual(len(json.loads(self.req('/v1/native/catalog')[1])['models']),5)
    def test_media_requires_token(self):
        with patch.object(application.bridge,'REMOTE_TOKEN','testing'):
            for path in ('/v1/image/file?job_id=foo','/v1/video/file?job_id=foo','/v1/native/media?job_id=foo','/v1/native/result?job_id=foo'):
                with self.subTest(path=path): self.assertEqual(self.req(path)[0],401)
    def test_authorized_catalog(self):
        with patch.object(application.bridge,'REMOTE_TOKEN','testing'):
            self.assertEqual(self.req('/v1/native/catalog',{'Authorization':'Bearer testing'})[0],200)
    def test_untrusted_origin(self): self.assertEqual(self.req('/v1/native/catalog',{'Origin':'https://untrusted.example'})[0],403)
    def test_preflight_without_token(self):
        with patch.object(application.bridge,'REMOTE_TOKEN','testing'):
            self.assertEqual(self.req('/v1/native/start',{'Origin':'http://localhost:8000'},'OPTIONS')[0],204)
    def test_bad_json(self): self.assertEqual(self.req('/v1/native/start',{'Content-Type':'application/json'},'POST','not json')[0],400)
    def test_nonobject_json(self): self.assertEqual(self.req('/v1/native/start',{},'POST','[]')[0],400)
    def test_body_limit(self): self.assertEqual(self.req('/v1/native/start',{'Content-Length':'16000001'},'POST')[0],400)
    def test_native_unknown_route(self): self.assertEqual(self.req('/v1/native/not-found',{},'POST','{}')[0],404)


if __name__=='__main__': unittest.main()
