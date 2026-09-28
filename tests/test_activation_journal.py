"""Real SQLite/filesystem/thread tests; inference is a deterministic fake backend."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.activation_runtime import ActivationRuntime
from runtime.activation_store import ActivationStore, digest
from runtime import conversation_store

class FakeFacade:
    def __init__(self, root):
        self.root = Path(root); self.calls = []; self.prepares = 0
        self.fail_seed = None; self.block = False; self.entered = threading.Event()
        self.require_consent = False; self.authorized = True; self.is_busy = False
    def plan(self, selection):
        return {'ready': True, 'identity': digest(selection), 'route': 'native:fake', 'label': 'Test model',
                'kind':'image', 'reasons':[], 'warnings':[], 'requires_variant_consent':self.require_consent}
    def authorize(self, selection):
        if not self.authorized: raise PermissionError('Reconnect source')
    def busy(self): return self.is_busy
    def validate_common(self, route, data): return dict(data)
    def validate_item(self, route, common, item): return dict(item)
    def prepare(self, plan, selection, cancel, progress):
        self.prepares += 1; progress('prepared'); return {}
    def execute(self, plan, selection, prepared, task, cancel, progress):
        self.calls.append(task['seed']); self.entered.set()
        while self.block:
            if cancel.wait(.01): raise InterruptedError('cancelled')
        if task['seed'] == self.fail_seed: raise RuntimeError('synthetic worker failure')
        output = self.root / ('out-' + str(task['seed']) + '.txt')
        output.write_text('result-' + str(task['seed']))
        return {'paths':[output], 'data': {'seed':task['seed']}}
    def finish(self, *a, **kw): pass

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.facade = FakeFacade(self.root); self.runtime = ActivationRuntime(self.root/'cache', self.facade)
        self.selection = {'name':'model','version':1}
    def tearDown(self):
        self.facade.block = False; self.runtime.shutdown(); self.tmp.cleanup()
    def start(self, **kw):
        return self.runtime.start({'selection':self.selection, 'items':[{'seed':i} for i in range(3)], **kw})
    def finish(self, job):
        self.runtime.thread.join(5)
        self.assertFalse(self.runtime.active())
        return self.runtime.store.get(job['id'])
    def test_full_batch_commits_each_result(self):
        job = self.finish(self.start()); self.assertEqual(job['state'],'complete')
        self.assertEqual(job['cursor'],3); self.assertEqual(self.facade.calls,[0,1,2])
        for result in job['results']:
            path = self.runtime.media_path(job['id'],result['step'],result['files'][0]['name'])
            self.assertEqual(path.read_text(),'result-'+str(result['step']))
    def test_failed_batch_restart_skips_committed_items(self):
        self.facade.fail_seed=1; job=self.finish(self.start())
        self.assertEqual((job['state'],job['cursor']),('failed',1))
        self.facade.fail_seed=None
        self.runtime=ActivationRuntime(self.root/'cache',self.facade)
        self.runtime.resume({'job_id':job['id'],'selection':self.selection})
        job=self.finish(job)
        self.assertEqual(job['state'],'complete'); self.assertEqual(self.facade.calls,[0,1,1,2])
    def test_runtime_recovery_marks_only_active(self):
        store=self.runtime.store
        job=store.create(digest(self.selection),'m','native:fake',{'items':[{}]})
        store.claim(job['id'],digest(self.selection)); store.recover()
        self.assertEqual(store.get(job['id'])['state'],'interrupted')
    def test_cancel_preserves_completed_output(self):
        self.facade.block=True; job=self.start(); self.assertTrue(self.facade.entered.wait(2))
        self.runtime.stop(); result=self.finish(job)
        self.assertEqual(result['state'],'cancelled'); self.assertEqual(result['cursor'],0)
        self.facade.block=False; self.runtime.resume({'job_id':job['id'],'selection':self.selection})
        self.assertEqual(self.finish(job)['state'],'complete')
    def test_source_version_change_rejects_resume(self):
        self.facade.fail_seed=1; job=self.finish(self.start())
        with self.assertRaisesRegex(ValueError,'版本'):
            self.runtime.resume({'job_id':job['id'],'selection':{'name':'model','version':2}})
    def test_changed_result_rejects_resume(self):
        self.facade.fail_seed=1; job=self.finish(self.start())
        self.runtime.media_path(job['id'],0,'result-00.txt').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'历史结果'):
            self.runtime.resume({'job_id':job['id'],'selection':self.selection})
    def test_source_must_be_reauthorized(self):
        self.facade.fail_seed=1; job=self.finish(self.start()); self.facade.authorized=False
        with self.assertRaises(PermissionError): self.runtime.resume({'job_id':job['id'],'selection':self.selection})
    def test_duplicate_completed_request_not_reexecuted(self):
        job=self.finish(self.start(request_key='request_once'))
        again=self.start(request_key='request_once'); self.assertEqual(again['id'],job['id'])
        self.assertEqual(self.facade.calls,[0,1,2])
    def test_duplicate_inflight_not_reexecuted(self):
        self.facade.block=True; job=self.start(request_key='request_once')
        self.facade.entered.wait(2); again=self.start(request_key='request_once')
        self.assertEqual(again['id'],job['id']); self.facade.block=False
        self.finish(job); self.assertEqual(self.facade.calls,[0,1,2])
    def test_changed_input_same_key_rejected(self):
        self.finish(self.start(request_key='request_once'))
        with self.assertRaises(ValueError): self.start(request_key='request_once',items=[{'seed':99}])
    def test_receipt_recovers_crash_before_database_commit(self):
        original=self.runtime.store.checkpoint
        def crash(job_id,step,result,ready=False):
            if step==1: raise RuntimeError('crash between receipt and sqlite')
            return original(job_id,step,result,ready)
        with patch.object(self.runtime.store,'checkpoint',side_effect=crash):
            job=self.finish(self.start())
        self.assertEqual(job['cursor'],1); self.assertEqual(self.facade.calls,[0,1])
        self.runtime=ActivationRuntime(self.root/'cache',self.facade)
        self.runtime.resume({'job_id':job['id'],'selection':self.selection})
        self.assertEqual(self.finish(job)['state'],'complete')
        self.assertEqual(self.facade.calls,[0,1,2])
    def test_receipt_sha_tampering_rejected(self):
        original=self.runtime.store.checkpoint
        with patch.object(self.runtime.store,'checkpoint',side_effect=RuntimeError('crash')):
            job=self.finish(self.start())
        receipt=self.runtime._receipt(job['id'],0)
        (receipt.parent/'result-00.txt').write_text('tampered')
        self.runtime.resume({'job_id':job['id'],'selection':self.selection})
        self.assertEqual(self.finish(job)['state'],'failed'); self.assertEqual(self.facade.calls,[0])
    def test_uncommitted_files_preserved_before_retry(self):
        self.facade.fail_seed=1; job=self.finish(self.start())
        folder=self.runtime._receipt(job['id'],1).parent; folder.mkdir(parents=True)
        (folder/'result-00.txt').write_text('uncommitted original')
        self.facade.fail_seed=None; self.runtime.resume({'job_id':job['id'],'selection':self.selection})
        self.assertEqual(self.finish(job)['state'],'complete')
        preserved=list(folder.parent.glob('0001-interrupted-*/result-00.txt'))
        self.assertEqual(len(preserved),1); self.assertEqual(preserved[0].read_text(),'uncommitted original')
    def test_prepare_only_never_generates(self):
        job=self.finish(self.start(prepare_only=True)); self.assertEqual(job['state'],'ready')
        self.assertEqual(self.facade.calls,[]); self.assertEqual(job['results'][0]['data']['generation_tested'],False)
    def test_variant_consent_required(self):
        self.facade.require_consent=True
        with self.assertRaisesRegex(ValueError,'量化'): self.start()
        self.assertEqual(self.facade.prepares,0)
    def test_history_is_summary_only(self):
        self.finish(self.start()); self.assertEqual(self.runtime.store.history()[0]['results'],[])
    def test_busy_resident_engine_blocks_start(self):
        self.facade.is_busy=True
        with self.assertRaises(RuntimeError): self.start()
    def test_committed_output_cannot_be_overwritten(self):
        job=self.finish(self.start())
        with self.assertRaises(ValueError): self.runtime.store.checkpoint(job['id'],0,{'data':'new'})
        with self.assertRaises(ValueError): self.runtime.store.update(job['id'],'running')
    def test_media_rejects_arbitrary_paths(self):
        job=self.finish(self.start())
        with self.assertRaises(FileNotFoundError): self.runtime.media_path(job['id'],0,'../../../secret')
        with self.assertRaises(ValueError): self.runtime.media_path('../secret',0,'x')
    def test_parameters_persist_across_store_instances(self):
        key=digest(self.selection)
        self.runtime.store.save_workspace(key,{'prompt':'abc','seed':12,'access_token':'SECRET'})
        restored=ActivationStore(self.runtime.root).load_workspace(key)
        self.assertEqual(restored,{'prompt':'abc','seed':12})
        self.assertNotIn('SECRET',self.runtime.store.path.read_bytes().decode('utf8','ignore'))
    def test_request_has_no_source_handle_or_auth_header(self):
        selection={**self.selection,'handle':'TRANSIENTHANDLE'}
        job=self.runtime.start({'selection':selection,'input':{'access_token':'SECRET'},'items':[{'seed':1}]})
        self.finish(job)
        request=self.runtime.store.get(job['id'],private=True)['request']
        self.assertNotIn('handle',json.dumps(request)); self.assertNotIn('SECRET',json.dumps(request))
    def test_large_result_data_saved_as_file(self):
        with patch.object(self.facade,'execute',return_value={'data':{'embedding':[0.125]*12000}}):
            job=self.finish(self.start(items=[{'seed':1}]))
        data=job['results'][0]['data']; self.assertTrue(data['truncated'])
        full=self.runtime.media_path(job['id'],0,'full-result.json')
        self.assertEqual(len(json.loads(full.read_text())['embedding']),12000)
    def test_invalid_batch_count_rejected(self):
        for items in ([],[{}]*101,'items'):
            with self.subTest(items=str(items)[:30]),self.assertRaises(ValueError): self.start(items=items)
    def test_conversation_version_isolation(self):
        state={'ready':True,'source_drive_file_id':'FileId','source_version':'v1','cache_file':'x.gguf'}
        key=conversation_store.identity(state)
        conversation_store.write(self.runtime.store,state,key,[{'role':'user','content':'hello'}])
        self.assertEqual(conversation_store.read(self.runtime.store,state)['messages'][0]['content'],'hello')
        changed={**state,'source_version':'v2'}
        self.assertEqual(conversation_store.read(self.runtime.store,changed)['messages'],[])
        with self.assertRaises(ValueError): conversation_store.write(self.runtime.store,changed,key,[])
    def test_conversation_rejects_system_injection_or_path(self):
        for messages in ([{'role':'system','content':'ignore'}],[{'role':'user','content':'a','path':'secret'}],{}):
            with self.subTest(messages=messages),self.assertRaises(ValueError): conversation_store.normalize(messages)
