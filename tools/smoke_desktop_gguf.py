"""Actual Qwen inference via a local-directory fixture and the public bridge API.

Run AFTER smoke_gguf_models on Windows so the verified official model/engine are
reused. Google Drive Desktop/DriveFS is not installed in this test.
"""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
base=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'model-desktop-smoke'
base.mkdir(exist_ok=True)
source_model=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'model-gguf-smoke/chat/Qwen3-0.6B-Q8_0.gguf'
if not source_model.is_file():raise RuntimeError('Run tools.smoke_gguf_models first')
bundle=json.loads((root/'runtime/llama/BUNDLE.json').read_text())
os.environ.update(LLAMA_SERVER_PATH=str(root/'runtime/llama'/bundle['executable']),MODEL_CACHE_ROOT=str(base/'cache'),MODEL_DESKTOP_CONFIG=str(base/'settings.json'),MODEL_THREADS='2')
from runtime import application as app
from runtime.chat_profiles import save_profile
vault=base/'Drive desktop fixture/AI-Model-Vault'
folder=vault/'llm/Desktop Qwen';folder.mkdir(parents=True,exist_ok=True)
model=folder/source_model.name
shutil.copy2(source_model,model)
app.DESKTOP.configure(str(vault))
server=ThreadingHTTPServer(('127.0.0.1',8765),app.ApplicationHandler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()

def call(path,body=None):
    connection=http.client.HTTPConnection('127.0.0.1',8765,timeout=180)
    connection.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers={'Content-Type':'application/json','Origin':'http://127.0.0.1:8765'})
    response=connection.getresponse();data=json.loads(response.read());status=response.status;connection.close()
    if status>=400:raise RuntimeError((status,data))
    return data

try:
    app.bridge.DRIVE_SESSION.clear()
    call('/v1/desktop/scan',{})
    deadline=time.monotonic()+30
    while call('/v1/desktop/status')['phase']!='complete':
        if time.monotonic()>deadline:raise TimeoutError('Desktop scan')
        time.sleep(.1)
    snapshot=call('/v1/desktop/snapshot')
    files=[]
    def walk(node):
        if node['file']['name'].endswith('.gguf'):files.append((node['file'],node['relativePath']))
        for child in node.get('children',[]):walk(child)
    walk(snapshot['tree']);record,relative=files[0]
    payload={'drive_file_id':record['id'],'file_name':record['name'],'size':int(record['size']),'display_name':'Desktop Qwen','name':'Desktop Qwen','relative_path':relative,'backend':'llama.cpp','category':'llm','format':'GGUF'}
    save_profile('Desktop Qwen',relative,{'context':2048,'threads':2,'gpu_layers':0,'load_mode':'none'})
    replies=[]
    with patch('runtime.drive_cache.urlopen',side_effect=AssertionError('Desktop source must not call Google Drive API')):
        for iteration in ('cold-copy','warm-cache'):
            call('/v1/models/start',payload)
            deadline=time.monotonic()+180
            while True:
                state=call('/v1/runtime')
                if state['ready']:break
                if state['phase']=='failed' or time.monotonic()>deadline:raise RuntimeError(state)
                time.sleep(.2)
            assert state['drive_api_session'] is False
            result=call('/v1/chat/completions',{'messages':[{'role':'user','content':'Reply with READY. /no_think'}],'max_tokens':64,'temperature':0})
            text=result['choices'][0]['message']['content']
            assert text.strip()
            replies.append({'iteration':iteration,'text':text,'drive_api_session':state['drive_api_session']})
            call('/v1/models/stop',{})
    assert model.is_file() and model.stat().st_size==source_model.stat().st_size
    report={'passed':True,'runtime_version':18,'source':'authorized temporary local-directory fixture','real_model_inference':True,'actual_drive_desktop_or_drivefs_tested':False,'web_oauth_required':False,'original_file_preserved':True,'results':replies}
    evidence=root/'qa-evidence';evidence.mkdir(exist_ok=True)
    (evidence/'desktop-real-chat.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report))
finally:
    app.bridge.STATE.stop();server.shutdown();server.server_close();thread.join(3)
