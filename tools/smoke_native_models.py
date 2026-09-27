"""Real CPU model smoke tests using ephemeral official weights, never the user's Drive."""
import argparse
import base64
import io
import json
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
from runtime.managed_env import ensure_environment
from runtime.native_worker import CATALOG
from runtime.native_runtime import verify_package

parser=argparse.ArgumentParser()
parser.add_argument('--model',choices=['got_ocr2','chronos_2','timesfm_2_0_500m'],required=True)
args=parser.parse_args()
root=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'model-native-smoke'/args.model
root.mkdir(parents=True,exist_ok=True)
repo=CATALOG[args.model]['repo']
revision=HfApi().model_info(repo).sha
folder=Path(snapshot_download(repo,revision=revision,local_dir=root/'weights',allow_patterns=['*.json','*.safetensors','*.model','*.txt','*.tiktoken','torch_model.ckpt']))
verify_package(args.model,folder)
if args.model=='got_ocr2':
    from PIL import Image, ImageDraw, ImageFont
    image=Image.new('RGB',(700,150),'white')
    fontfile='C:/Windows/Fonts/arial.ttf' if os.name=='nt' else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    font=ImageFont.truetype(fontfile,44)
    ImageDraw.Draw(image).text((25,45),'MODEL TEST 123',font=font,fill='black')
    stream=io.BytesIO();image.save(stream,format='PNG')
    payload={'image':base64.b64encode(stream.getvalue()).decode(),'max_new_tokens':64}
else:
    payload={'values':[float(10+0.01*i+math.sin(i/7)) for i in range(128)],'horizon':8,'frequency':0}
interpreter=ensure_environment(root,args.model,threading.Event(),print)
output=root/'output';output.mkdir(exist_ok=True)
worker=Path(__file__).resolve().parents[1]/'runtime/native_worker.py'
request={'model_id':args.model,'model_dir':str(folder),'output_dir':str(output),'input':payload}
env={key:value for key,value in os.environ.items() if not any(word in key.upper() for word in ['TOKEN','SECRET','API_KEY'])}
env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
subprocess.run([str(interpreter),str(worker)],input=json.dumps(request),text=True,env=env,check=True,timeout=1200)
result=json.loads((output/'result.json').read_text(encoding='utf-8'))
assert result['model_id']==args.model
if args.model=='got_ocr2':
    text=result.get('text','').upper()
    assert 'MODEL' in text and '123' in text,repr(text)
else:
    assert len(result['rows'])==8
    assert all(math.isfinite(row['prediction']) for row in result['rows'])
evidence=Path('qa-evidence');evidence.mkdir(exist_ok=True)
report={'model_id':args.model,'repo':repo,'revision':revision,'status':'PASS','execution':'actual CPU inference','drive_flow_tested':False,'intended_user_device_tested':False,'result':result}
(evidence/(args.model+'.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
