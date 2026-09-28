"""Offline Chromium UI + real localhost HTTP; source/inference are fixtures.

The test environment disallows browser URL navigation. HTML is loaded using
set_content and only fetch is bridged to an in-process localhost test server.
This tests production activation.js against production HTTP/SQLite handlers,
not Google Drive OAuth, full scanner navigation, Windows, or GPU inference.
"""
import base64
import http.client
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    os.environ['MODEL_CACHE_ROOT']=str(root/'cache')
    from runtime import application
    from runtime.activation_runtime import ActivationRuntime
    from runtime.activation_store import digest
    class FixtureFacade:
        fail=124
        calls=[]
        def plan(self,selection):
            return dict(identity=digest(selection),route='native:fixture_image',label='Fixture model',kind='image',ready=True,reasons=[],warnings=['Fixture inference only'],requires_variant_consent=False)
        def authorize(self,selection): pass
        def busy(self): return False
        def validate_common(self,route,value):return dict(value)
        def validate_item(self,route,common,item):return dict(item)
        def prepare(self,plan,selection,cancel,progress):progress('Fixture cache prepared');return {}
        def execute(self,plan,selection,prepared,task,cancel,progress):
            seed=task['seed'];self.calls.append(seed)
            if self.fail==seed:raise RuntimeError('Fixture interruption before second image')
            path=root/f'out-{seed}.txt';path.write_text('Fixture output '+str(seed))
            return dict(paths=[path],data={'seed':seed,'inference_tested':False})
        def finish(self,*a,**kw):pass
    facade=FixtureFacade();application.ACTIVATION=ActivationRuntime(root/'cache',facade)
    server=ThreadingHTTPServer(('127.0.0.1',0),application.ApplicationHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    evidence=Path(os.environ.get('EVIDENCE_DIR','qa-evidence/activation'));evidence.mkdir(parents=True,exist_ok=True)
    errors=[];requests=[]
    def bridge(args):
        path,options=args;requests.append(path)
        assert path.startswith('/v1/activation/')
        headers={**options.get('headers',{}),'Origin':'http://127.0.0.1:8765'}
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        conn.request(options.get('method','GET'),path,body=options.get('body'),headers=headers)
        response=conn.getresponse();raw=response.read()
        value={'status':response.status,'body':base64.b64encode(raw).decode(),'type':response.headers.get('Content-Type','application/octet-stream')}
        conn.close();return value
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True)
            def load(storage=None):
                page=browser.new_page(viewport={'width':1280,'height':1000})
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.expose_function('testHTTPBridge',bridge)
                page.set_content('<html lang="zh"><body><main class="app"></main></body></html>')
                page.add_style_tag(content=(Path('assets/model.css').read_text()))
                page.evaluate('''storage => {
                  const values=new Map(Object.entries(storage||{}));
                  Object.defineProperty(window,'localStorage',{value:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)}});
                  window.testStorage=values;
                  if(!crypto.randomUUID)crypto.randomUUID=()=> 'fixture_'+String(Date.now());
                  const model={id:'fixture',name:'Fixture model',category:'image',backend:'fixture',packagePath:'image/test',fileCount:1,totalSize:10,files:[{id:'fixtureFileID',name:'file.safetensors',relativePath:'file.safetensors',size:10}]};
                  window.ModelApp={models:()=>[model],source:()=> 'desktop',refreshRuntime:async()=>{},runtimeFetch:async(path,options={})=>{
                    const r=await testHTTPBridge([path,options]);
                    return new Response(Uint8Array.from(atob(r.body),c=>c.charCodeAt(0)),{status:r.status,headers:{'Content-Type':r.type}});
                  }};
                }''',storage or {})
                page.add_script_tag(content=Path('assets/activation.js').read_text())
                return page
            page=load();page.select_option('#activationModel','0')
            page.wait_for_function("!document.querySelector('#activationRun').disabled")
            page.fill('#activationPrompt','Keep the same adult character. <img src=x onerror=alert(1)>')
            page.fill('#activation_seed','123');page.fill('#activationBatch','standing\nsitting')
            page.click('#activationSave')
            page.wait_for_function("document.querySelector('#activationStatus').textContent.includes('参数已保存')")
            storage=page.evaluate('Object.fromEntries(testStorage)')
            page.click('#activationRun')
            page.wait_for_function("document.querySelector('#activationStatus').textContent.includes('失败')",timeout=8000)
            page.wait_for_selector('#activationHistory button:text("接续未完成项")')
            assert facade.calls==[123,124],facade.calls
            assert page.locator('#activationResults article').count()==1
            page.screenshot(path=str(evidence/'activation-interrupted.png'))
            page.close()
            # Simulate Runtime/process re-creation, preserving SQLite and result files.
            application.ACTIVATION=ActivationRuntime(root/'cache',facade);facade.fail=None
            page=load(storage)
            page.wait_for_function("!document.querySelector('#activationRun').disabled")
            assert page.input_value('#activation_seed')=='123'
            assert 'Keep the same adult' in page.input_value('#activationPrompt')
            page.get_by_text('接续未完成项',exact=True).click()
            page.wait_for_function("document.querySelector('#activationStatus').textContent.includes('已完成')",timeout=8000)
            assert facade.calls==[123,124,124],facade.calls
            page.wait_for_function("document.querySelectorAll('#activationResults article').length===2")
            assert page.locator('img[src="x"]').count()==0
            page.screenshot(path=str(evidence/'activation-complete.png'))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(evidence/'activation-mobile.png'),full_page=True)
            assert not errors,errors
            browser.close()
        report={'passed':True,'checks':['select-preflight','save-parameters','two-item-batch','persist-first-result','failure-history','runtime-recreation','restore-parameters','resume-only-unfinished','escape-user-text','mobile-390-no-overflow','zero-page-errors'],
                'backend_calls':facade.calls,'transport':'real localhost HTTP via test-only fetch bridge','browser':'Chromium offline DOM; administrator policy blocks URL navigation',
                'actual_drivefs_tested':False,'gpu_inference_tested':False,'windows_tested':False,'page_errors':errors}
        (evidence/'activation-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
    finally:
        application.ACTIVATION.shutdown();server.shutdown();server.server_close();thread.join(3)
