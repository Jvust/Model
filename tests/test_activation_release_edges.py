"""Release-edge tests: no real weights, network, CUDA, or Windows processes."""
import copy
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from runtime.activation_backends import ActivationBackends, selection_payload
from runtime.activation_runtime import ActivationRuntime
from runtime.drive_cache import DriveCache, DriveFileSpec
from runtime.image_runtime import extract_node_archive, COMFY_GGUF_ARCHIVE_URL, COMFY_GGUF_COMMIT
from runtime.native_runtime import verify_package
from test_activation_integrity import safe_bytes, selection
from test_activation_journal import FakeFacade

class ReleaseEdges(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def flux(self, distilled=True, hidden=2560):
        for folder in ('transformer','text_encoder','vae'):
            path=self.root/folder; path.mkdir(exist_ok=True)
            (path/'w.safetensors').write_bytes(safe_bytes())
            (path/'config.json').write_text(json.dumps({'hidden_size':hidden} if folder=='text_encoder' else {'guidance_embeds':False}))
        (self.root/'model_index.json').write_text(json.dumps({'_class_name':'Flux2KleinPipeline','is_distilled':distilled}))
    def test_verified_flux_distilled_profile(self):
        self.flux(); verify_package('flux2_klein_4b_diffusers', self.root)
    def test_non_distilled_flux_not_labeled_as_four_step(self):
        self.flux(False)
        with self.assertRaisesRegex(ValueError,'Wrong pipeline'): verify_package('flux2_klein_4b_diffusers', self.root)
    def test_nine_b_encoder_rejected(self):
        self.flux(hidden=4096)
        with self.assertRaisesRegex(ValueError,'4B'): verify_package('flux2_klein_4b_diffusers', self.root)
    def test_size_fraction_and_boolean_rejected(self):
        for size in (True,1.5,'1.5'):
            with self.subTest(size=size),self.assertRaises(ValueError):
                DriveFileSpec.from_payload({'drive_file_id':'abcdefghijk','size':size})
    def test_selected_zero_false_size_rejected(self):
        p=selection(); p['manifest_files'][0]['size']=False
        with self.assertRaises(ValueError): selection_payload(p)
    def test_variant_revision_binds_durable_identity(self):
        facade=ActivationBackends(SimpleNamespace(DRIVE_CACHE=DriveCache(self.root)), Mock(), Mock())
        manifest={'files':[{'revision':'a'*40,'sha256':'b'*64}]}
        facade.variants=Mock();facade.variants.manifest.return_value=manifest
        payload=selection_payload(selection('Qwen-Image-Edit-2511', variant='qwen2511_gguf_q4km'))
        first=facade.identity(payload,'image:qwen_image_edit_2511_gguf')
        manifest['files'][0]['revision']='c'*40
        self.assertNotEqual(first,facade.identity(payload,'image:qwen_image_edit_2511_gguf'))
    def test_concurrent_new_key_does_not_leave_orphan_job(self):
        facade=FakeFacade(self.root);facade.block=True
        rt=ActivationRuntime(self.root/'cache',facade)
        body={'selection':{'name':'x'},'items':[{'seed':0}],'request_key':'request_key_one'}
        try:
            rt.start(body); self.assertTrue(facade.entered.wait(2))
            with self.assertRaises(RuntimeError): rt.start({**body,'request_key':'request_key_two'})
            self.assertEqual(len(rt.store.history()),1)
        finally:
            facade.block=False;rt.shutdown()
    def archive(self, members):
        path=self.root/'node.zip'
        with zipfile.ZipFile(path,'w') as zf:
            for name,content in members: zf.writestr(name,content)
        return path
    def test_source_archive_extracts_fixed_tree(self):
        zip_path=self.archive([('ComfyUI-GGUF/x.py','print(1)')]); dest=self.root/'out';dest.mkdir()
        extract_node_archive(zip_path,dest)
        self.assertEqual((dest/'ComfyUI-GGUF/x.py').read_text(),'print(1)')
    def test_source_archive_traversal_rejected_before_writing(self):
        archive=self.archive([('ok.py','ok'),('../escape.py','bad')]);dest=self.root/'out';dest.mkdir()
        with self.assertRaises(ValueError):extract_node_archive(archive,dest)
        self.assertEqual(list(dest.iterdir()),[])
    def test_source_archive_symlink_rejected(self):
        zi=zipfile.ZipInfo('link.py');zi.create_system=3;zi.external_attr=(stat.S_IFLNK|0o777)<<16
        archive=self.archive([(zi,'../other')]);dest=self.root/'out';dest.mkdir()
        with self.assertRaises(ValueError):extract_node_archive(archive,dest)
    def test_source_archive_case_collision_rejected(self):
        archive=self.archive([('x.py','1'),('X.py','2')]);dest=self.root/'out';dest.mkdir()
        with self.assertRaises(ValueError):extract_node_archive(archive,dest)
    def test_node_url_pins_commit_not_branch(self):
        self.assertEqual(len(COMFY_GGUF_COMMIT),40)
        self.assertIn(COMFY_GGUF_COMMIT,COMFY_GGUF_ARCHIVE_URL); self.assertNotIn('main.zip',COMFY_GGUF_ARCHIVE_URL)
    def test_windows_launcher_always_checks_executable_signature(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'tools/run_candidate.ps1').read_text()
        self.assertIn("# Always verify, including reuse",script)
        self.assertGreater(script.index('$signature = Get-AuthenticodeSignature'),script.index('Remove-Item -LiteralPath $archive -Force'))
        self.assertLess(script.index('$signature = Get-AuthenticodeSignature'),script.index('& $python'))
