"""Malformed checkpoint/source tests without allocating any real model tensors."""
import base64
import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from runtime.package_integrity import (safe_relative, read_json, shard_references,
    check_index_manifest, check_safetensors, verify_directory, MAX_HEADER)
from runtime.activation_backends import selection_payload, strip_model_wrapper, ActivationBackends
from runtime.qwen_edit_workflow import build_qwen_edit, QWEN_EDIT_ADAPTER
from runtime.native_worker import validate_input
from runtime.drive_cache import DriveCache
from runtime.native_runtime import NativeRuntime, verify_package


def safe_bytes(data=b'abcd', header=None):
    header = header or {'test':{'dtype':'U8','shape':[len(data)],'data_offsets':[0,len(data)]}}
    raw=json.dumps(header,separators=(',',':')).encode()
    return struct.pack('<Q',len(raw))+raw+data

def selection(name='Unknown', files=('model.gguf',), **kw):
    manifest=[{'file_name':Path(f).name,'relative_path':f,'drive_file_id':'ValidDriveID%03d'%i,'size':100,
               'md5_checksum':'a'*32,'modified_time':'v1'} for i,f in enumerate(files)]
    return {'name':name,'files':manifest,'manifest_files':manifest,'package_path':'llm/test',**kw}

class IntegrityTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_safe_nested_path(self): self.assertEqual(safe_relative('encoder/model-01.safetensors'),'encoder/model-01.safetensors')
    def test_unsafe_paths(self):
        for path in ('','/x','C:/x','../x','a/../x','x\\y','a//b','a/./b','a/NUL.gguf','a/com1','a.','a ','x\0y'):
            with self.subTest(path=path),self.assertRaises(ValueError):safe_relative(path)
    def test_json_must_be_dictionary(self):
        p=self.root/'config.json';p.write_text('[]')
        with self.assertRaises(ValueError):read_json(p)
    def test_source_manifest_validates_each_index_shard(self):
        doc={'weight_map':{'a':'shard1.safetensors','b':'shard2.safetensors'}}
        with self.assertRaisesRegex(ValueError,'shard2'):check_index_manifest('transformer/model.index.json',doc,{'transformer/shard1.safetensors'})
        self.assertEqual(len(shard_references('transformer/model.index.json',doc)),2)
    def test_index_rejects_traversal(self):
        with self.assertRaises(ValueError):shard_references('model.index.json',{'weight_map':{'a':'../outside.safetensors'}})
    def test_index_rejects_empty_or_non_weights(self):
        for mapping in ({},[],{'a':'script.py'}):
            with self.subTest(mapping=mapping),self.assertRaises(ValueError):shard_references('model.index.json',{'weight_map':mapping})
    def test_safetensors_valid_header(self):
        p=self.root/'model.safetensors';p.write_bytes(safe_bytes())
        self.assertEqual(check_safetensors(p)['tensor_count'],1)
    def test_safetensors_short_file(self):
        p=self.root/'bad.safetensors';p.write_bytes(b'123')
        with self.assertRaises(ValueError):check_safetensors(p)
    def test_safetensors_oversized_header(self):
        p=self.root/'bad.safetensors';p.write_bytes(struct.pack('<Q',MAX_HEADER+1))
        with self.assertRaises(ValueError):check_safetensors(p)
    def test_safetensors_truncation(self):
        p=self.root/'bad.safetensors';p.write_bytes(safe_bytes()[:-1])
        with self.assertRaises(ValueError):check_safetensors(p)
    def test_safetensors_trailing_bytes(self):
        p=self.root/'bad.safetensors';p.write_bytes(safe_bytes()+b'x')
        with self.assertRaises(ValueError):check_safetensors(p)
    def test_safetensors_overlap(self):
        h={k:{'dtype':'U8','shape':[3],'data_offsets':span} for k,span in [('a',[0,3]),('b',[2,4])]}
        p=self.root/'bad.safetensors';p.write_bytes(safe_bytes(header=h))
        with self.assertRaises(ValueError):check_safetensors(p)
    def test_completion_marker_not_proof(self):
        (self.root/'.download_complete.json').write_text('{"status":"complete"}')
        (self.root/'model.index.json').write_text(json.dumps({'weight_map':{'w':'absent.safetensors'}}))
        with self.assertRaisesRegex(ValueError,'absent'):verify_directory(self.root)
    def test_zero_length_shard_rejected(self):
        (self.root/'model.index.json').write_text(json.dumps({'weight_map':{'w':'empty.safetensors'}}))
        (self.root/'empty.safetensors').touch()
        with self.assertRaisesRegex(ValueError,'Empty'):verify_directory(self.root)
    def test_expected_size_checked(self):
        (self.root/'model.gguf').write_bytes(b'GGUF')
        with self.assertRaisesRegex(ValueError,'size mismatch'):verify_directory(self.root,{'model.gguf':8})
    def test_valid_structure_not_claimed_as_hash_or_inference(self):
        (self.root/'model.safetensors').write_bytes(safe_bytes())
        (self.root/'model.index.json').write_text(json.dumps({'weight_map':{'w':'model.safetensors'}}))
        report=verify_directory(self.root)
        self.assertEqual(report['indexed_shards'],1)
        self.assertFalse(report['tensor_checksums_verified']);self.assertFalse(report['model_execution_verified'])
    def test_symlink_file_rejected(self):
        file=self.root/'real';file.write_bytes(b'data');(self.root/'link.gguf').symlink_to(file)
        with self.assertRaises(ValueError):verify_directory(self.root)

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();cache=DriveCache(Path(self.tmp.name))
        self.bridge=SimpleNamespace(DRIVE_CACHE=cache)
        self.native=Mock();self.desktop=Mock();self.facade=ActivationBackends(self.bridge,self.native,self.desktop)
    def tearDown(self):self.tmp.cleanup()
    def test_unknown_safetensor_never_claimed_supported(self):
        report=self.facade.plan(selection(files=['m.safetensors']))
        self.assertFalse(report['ready']);self.assertEqual(report['route'],'unsupported')
    def test_image_gguf_is_not_chat(self):
        report=self.facade.plan(selection('Arbitrary image',category='image',backend='llama.cpp'))
        self.assertFalse(report['ready']);self.assertNotEqual(report['route'],'chat')
    def test_chat_requires_explicit_category_and_backend(self):
        payload=selection_payload(selection(category='llm',backend='llama.cpp'))
        route,target=self.facade._route(payload);self.assertEqual(route,'chat');self.assertEqual(target['file_name'],'model.gguf')
    def test_multiple_chat_quants_are_ambiguous(self):
        with self.assertRaises(ValueError): self.facade._route(selection_payload(selection(files=['a.gguf','b.gguf'],category='llm',backend='llama.cpp')))
    def test_full_qwen_does_not_silently_download_quant(self):
        plan=self.facade.plan(selection('Qwen-Image-Edit-2511',files=['transformer/m.safetensors']))
        self.assertFalse(plan['ready']);self.assertEqual(plan['supported_variant'],'qwen2511_gguf_q4km')
    def test_explicit_qwen_variant_route(self):
        payload=selection_payload(selection('Qwen-Image-Edit-2511',variant='qwen2511_gguf_q4km'))
        route,_=self.facade._route(payload);self.assertEqual(route,'image:qwen_image_edit_2511_gguf')
    def test_wrong_family_variant_rejected(self):
        with self.assertRaises(ValueError):self.facade._route(selection_payload(selection(variant='qwen2511_gguf_q4km')))
    def test_flux_notebook_wrapper_is_normalized(self):
        files=['model/model_index.json','model/transformer/config.json','inputs/a.png']
        route,target=self.facade._route(selection_payload(selection('FLUX2 Klein 4B v9',files=files)))
        self.assertEqual(route,'native:flux2_klein_4b_diffusers')
        self.assertEqual([f['relative_path'] for f in target['manifest_files']],['model_index.json','transformer/config.json'])
    def test_multiple_diffusers_roots_rejected(self):
        with self.assertRaises(ValueError):strip_model_wrapper(selection_payload(selection(files=['a/model_index.json','b/model_index.json'])))
    def test_identity_changes_with_same_size_source_md5(self):
        p=selection_payload(selection());first=self.facade.identity(p,'test')
        p=copy.deepcopy(p);p['manifest_files'][0]['md5_checksum']='b'*32
        self.assertNotEqual(first,self.facade.identity(p,'test'))
    def test_identity_excludes_credentials(self):
        p=selection_payload(selection());q=copy.deepcopy(p);q['manifest_files'][0]['resource_key']='secret'
        self.assertEqual(self.facade.identity(p,'test'),self.facade.identity(q,'test'))
    def test_case_collisions_rejected(self):
        with self.assertRaises(ValueError):self.facade.plan(selection(files=['a.gguf','A.gguf']))
    def test_unexpected_selection_fields_rejected(self):
        with self.assertRaises(ValueError):selection_payload(selection(command='powershell evil'))
    def test_files_need_positive_size(self):
        p=selection();p['manifest_files'][0]['size']=0
        with self.assertRaises(ValueError):selection_payload(p)

class QwenGraphTests(unittest.TestCase):
    def payload(self):return {'prompt':'Keep the same adult character; stand up','_image_names':['model_'+'0'*32+'_0.png']}
    def build(self,payload=None):return build_qwen_edit({k:v['name'] for k,v in QWEN_EDIT_ADAPTER['artifacts'].items()},payload or self.payload(),'1'*32)
    def test_fixed_edit_graph_includes_reference_conditioning(self):
        g=self.build();self.assertEqual(g['20']['class_type'],'TextEncodeQwenImageEditPlus')
        self.assertEqual(g['23']['inputs']['denoise'],1);self.assertEqual(g['2']['inputs']['device'],'cpu')
    def test_original_bf16_not_selected_as_gguf(self):self.assertTrue(self.build()['1']['inputs']['unet_name'].endswith('Q4_K_M.gguf'))
    def test_three_references(self):
        p=self.payload();p['_image_names']=['model_'+'0'*32+f'_{i}.png' for i in range(3)]
        self.assertIn('image3',self.build(p)['20']['inputs'])
    def test_user_file_path_cannot_enter_workflow(self):
        p=self.payload();p['_image_names']=['../../secret.png']
        with self.assertRaises(ValueError):self.build(p)
    def test_strict_scalars_and_lengths(self):
        for key,value in [('width',True),('height',512.5),('steps',1.5),('seed',False),('cfg',float('nan')),('prompt',{}),('negative_prompt','x'*6001)]:
            with self.subTest(key=key),self.assertRaises(ValueError):self.build({**self.payload(),key:value})
    def test_missing_reference_not_txt2img(self):
        p=self.payload();p['_image_names']=[]
        with self.assertRaises(ValueError):self.build(p)
    def test_flux_four_steps_not_undistilled(self):
        self.assertEqual(validate_input('flux2_klein_4b_diffusers',{'prompt':'edit'})['steps'],4)
        with self.assertRaises(ValueError):validate_input('flux2_klein_4b_diffusers',{'prompt':'edit','steps':20})
