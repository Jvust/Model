"""Actual chat/embedding/reranking using the bundled Windows CPU llama-server."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from huggingface_hub import HfApi, hf_hub_download

root=Path(__file__).resolve().parents[1]
bundle=json.loads((root/'runtime/llama/BUNDLE.json').read_text())
executable=root/'runtime/llama'/bundle['executable']
cache=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'model-gguf-smoke'
cache.mkdir(exist_ok=True)
os.environ.update(LLAMA_SERVER_PATH=str(executable),MODEL_CACHE_ROOT=str(cache/'cache'),MODEL_THREADS='2',MODEL_TASK_THREADS='2')
from runtime import local_bridge as bridge
from runtime.task_runtime import TASK_ADAPTERS, TaskRuntime, json_request
from runtime.chat_profiles import ProfiledRuntimeState, save_profile
from runtime.drive_cache import DriveFileSpec

MODELS=[
 ('chat','Qwen/Qwen3-0.6B-GGUF','Qwen3-0.6B-Q8_0.gguf'),
 ('qwen3_embedding_0_6b','Qwen/Qwen3-Embedding-0.6B-GGUF','Qwen3-Embedding-0.6B-Q8_0.gguf'),
 ('qwen3_reranker_0_6b','ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF','qwen3-reranker-0.6b-q8_0.gguf'),
]
records=[]
for kind,repo,name in MODELS:
    info=HfApi().model_info(repo,files_metadata=True)
    item=next(item for item in info.siblings if item.rfilename==name)
    path=Path(hf_hub_download(repo,name,revision=info.sha,local_dir=cache/kind))
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(8*1024*1024):digest.update(block)
    expected=getattr(item.lfs,'sha256',None)
    assert path.stat().st_size==item.size and expected==digest.hexdigest()
    if kind=='chat':
        runtime=ProfiledRuntimeState()
        save_profile('Smoke Qwen',name,{'context':2048,'threads':2,'gpu_layers':0,'load_mode':'none'})
        runtime.profile={'context':2048,'threads':2,'gpu_layers':0,'load_mode':'none'}
        try:
            runtime._launch_cached(runtime.job_id,path,'Smoke Qwen',name)
            deadline=time.monotonic()+180
            while not runtime.snapshot()['ready']:
                if time.monotonic()>deadline or runtime.snapshot()['phase']=='failed':raise RuntimeError(runtime.snapshot())
                time.sleep(.2)
            status,output=json_request('http://127.0.0.1:8080/v1/chat/completions',method='POST',payload={'messages':[{'role':'user','content':'Reply with the word READY. /no_think'}],'max_tokens':64,'temperature':0},timeout=120)
            assert status==200 and output['choices'][0]['message']['content'].strip()
            result={'text':output['choices'][0]['message']['content'],'context':runtime.snapshot()['profile']['context']}
        finally:runtime.stop()
    else:
        runtime=TaskRuntime(bridge.DRIVE_CACHE,lambda:'unused',lambda:str(executable))
        runtime.adapter_id=kind;runtime.kind=TASK_ADAPTERS[kind]['kind']
        try:
            runtime._launch(runtime.job_id,path,TASK_ADAPTERS[kind])
            deadline=time.monotonic()+180
            while not runtime.snapshot()['ready']:
                if time.monotonic()>deadline or runtime.snapshot()['phase']=='failed':raise RuntimeError(runtime.snapshot())
                time.sleep(.2)
            if runtime.kind=='embedding':
                output=runtime.embeddings({'input':['A cat is sleeping.','A dog runs in the park.']})
                vectors=[item['embedding'] for item in output['data']]
                assert len(vectors)==2 and all(len(v)==1024 for v in vectors)
                assert all(math.isfinite(number) for vector in vectors for number in vector)
                result={'count':len(vectors),'dimension':len(vectors[0]),'first_vector_preview':vectors[0][:12]}
            else:
                output=runtime.rerank({'query':'What is the capital of France?','documents':['Paris is the capital of France.','Bananas are yellow fruit.'],'top_n':2})
                rows=output.get('results',output.get('data',[]))
                assert len(rows)==2 and all(math.isfinite(row['relevance_score']) for row in rows)
                assert rows[0]['index']==0
                result=output
        finally:runtime.stop()
    records.append({'kind':kind,'repo':repo,'revision':info.sha,'sha256':digest.hexdigest(),'status':'PASS','execution':'real Windows CPU llama-server','drive_flow_tested':False,'result':result})
evidence=root/'qa-evidence';evidence.mkdir(exist_ok=True)
(evidence/'gguf-real.json').write_text(json.dumps({'runtime':bundle,'models':records},indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(records,ensure_ascii=False))
