"""Real HTTP boundary tests; deterministic backend, not inference acceptance."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from runtime import application
from runtime.activation_runtime import ActivationRuntime
from test_activation_journal import FakeFacade

class HTTPActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),application.ApplicationHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.facade=FakeFacade(self.root)
        self.runtime=ActivationRuntime(self.root/'cache',self.facade)
        self.patcher=patch.object(application,'ACTIVATION',self.runtime);self.patcher.start()
        self.selection={'name':'Test','version':1}
    def tearDown(self):
        self.facade.block=False;self.runtime.shutdown();self.patcher.stop();self.tmp.cleanup()
    def req(self,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        h={'Content-Type':'application/json',**(headers or {})}
        conn.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers=h)
        r=conn.getresponse();data=r.read();code=r.status;conn.close()
        return code,data
    def start(self,**kw):
        status,raw=self.req('/v1/activation/start',{'selection':self.selection,'items':[{'seed':1}],**kw})
        self.assertEqual(status,202,raw);return json.loads(raw)
    def test_start_poll_and_result(self):
        job=self.start();self.runtime.thread.join(3)
        status,raw=self.req('/v1/activation/job?job_id='+job['id']);self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['state'],'complete')
        status,raw=self.req('/v1/activation/file?job_id='+job['id']+'&step=0&name=result-00.txt')
        self.assertEqual((status,raw),(200,b'result-1'))
    def test_file_range(self):
        job=self.start();self.runtime.thread.join(3)
        status,raw=self.req('/v1/activation/file?job_id='+job['id']+'&step=0&name=result-00.txt',headers={'Range':'bytes=0-2'})
        self.assertEqual((status,raw),(206,b'res'))
    def test_untrusted_origin(self):
        status,_=self.req('/v1/activation/history',headers={'Origin':'https://attacker.example'})
        self.assertEqual(status,403)
    def test_remote_token_required_for_journal_and_result(self):
        with patch.object(application.bridge,'REMOTE_TOKEN','private'):
            for path in ['/v1/activation/history','/v1/activation/file?job_id='+'a'*32,'/v1/chat/history']:
                with self.subTest(path=path):self.assertEqual(self.req(path)[0],401)
            self.assertEqual(self.req('/v1/activation/status',headers={'Authorization':'Bearer private'})[0],200)
    def test_unknown_endpoint(self):self.assertEqual(self.req('/v1/activation/evil',{})[0],404)
    def test_unknown_request_field(self):self.assertEqual(self.req('/v1/activation/plan',{'selection':self.selection,'command':'exec'})[0],400)
    def test_nonobject_request(self):self.assertEqual(self.req('/v1/activation/start',[])[0],400)
    def test_workspace_survives_reload(self):
        body={'selection':self.selection,'value':{'prompt':'test','seed':42}}
        self.assertEqual(self.req('/v1/activation/workspace',body)[0],200)
        status,raw=self.req('/v1/activation/workspace',{'selection':self.selection})
        self.assertEqual(json.loads(raw)['value'],body['value'])
    def test_workspace_rejects_credentials(self):
        self.assertEqual(self.req('/v1/activation/workspace',{'selection':self.selection,'value':{'access_token':'SECRET'}})[0],400)
    def test_active_job_blocks_legacy_starts_and_source_changes(self):
        self.facade.block=True;self.start();self.facade.entered.wait(2)
        for path in ['/v1/models/start','/v1/models/cache/clear','/v1/desktop/pick','/v1/desktop/scan','/v1/image/generate','/v1/native/start']:
            with self.subTest(path=path):self.assertEqual(self.req(path,{})[0],409)
    def test_cancel_and_resume_over_http(self):
        self.facade.block=True;job=self.start();self.facade.entered.wait(2)
        self.assertEqual(self.req('/v1/activation/stop',{})[0],200);self.runtime.thread.join(3)
        self.facade.block=False
        self.assertEqual(self.req('/v1/activation/resume',{'job_id':job['id'],'selection':self.selection})[0],202)
        self.runtime.thread.join(3);self.assertEqual(self.runtime.store.get(job['id'])['state'],'complete')
    def test_invalid_job_id(self):self.assertEqual(self.req('/v1/activation/job?job_id=../evil')[0],400)
    def test_history_does_not_include_private_inputs(self):
        job=self.start(input={'prompt':'PRIVATE PROMPT'});self.runtime.thread.join(3)
        status,raw=self.req('/v1/activation/history');self.assertEqual(status,200)
        self.assertNotIn(b'PRIVATE PROMPT',raw);self.assertEqual(json.loads(raw)['jobs'][0]['results'],[])
