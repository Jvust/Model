"""Per-model context/compute settings with a tested llama-server command boundary."""
from __future__ import annotations
import functools
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
try:
    from . import local_bridge as bridge
except ImportError:
    import local_bridge as bridge

PROFILE_LOCK=threading.Lock()


def normalize_profile(value):
    if not isinstance(value,dict):raise ValueError('Profile must be an object')
    result={}
    for key,default,minimum,maximum in [('context',4096,512,32768),('threads',bridge.MODEL_THREADS,1,256),('gpu_layers',bridge.MODEL_GPU_LAYERS,0,256)]:
        raw=value.get(key,default)
        if isinstance(raw,bool):raise ValueError(key+' must be an integer')
        try:
            number=int(raw)
            if float(raw)!=number or not minimum<=number<=maximum:raise ValueError()
        except (TypeError,ValueError,OverflowError) as error:raise ValueError(key+' outside supported range') from error
        result[key]=number
    result['load_mode']=bridge.normalize_load_mode(value.get('load_mode',bridge.MODEL_LOAD_MODE))
    return result


def profile_path(name,relative):
    if not isinstance(name,str) or not name.strip() or len(name)>512:raise ValueError('Model name required')
    if not isinstance(relative,str) or len(relative)>4096:raise ValueError('Invalid relative identity')
    identity=hashlib.sha256(json.dumps([name,relative],ensure_ascii=False).encode()).hexdigest()
    return bridge.DRIVE_CACHE.root/'profiles'/(identity+'.json')


def load_profile(name,relative):
    path=profile_path(name,relative)
    if path.is_file():return normalize_profile(json.loads(path.read_text(encoding='utf-8'))['profile'])
    context=8192 if any(word in name.lower() for word in ('deepseek','coder','writing','novel','writer')) else 4096
    return normalize_profile({'context':context})


def save_profile(name,relative,value):
    profile=normalize_profile(value);path=profile_path(name,relative)
    with PROFILE_LOCK:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'name':name,'relative_path':relative,'profile':profile},ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
    return profile


@functools.lru_cache(maxsize=8)
def server_help(executable):
    result=subprocess.run([executable,'--help'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
    if result.returncode:raise RuntimeError('llama-server --help failed; select a compatible runtime')
    return result.stdout+'\n'+result.stderr


def command(executable,model_path,profile,help_text):
    profile=normalize_profile(profile)
    args=[executable,'-m',str(model_path),'--host','127.0.0.1','--port',str(bridge.MODEL_SERVER_PORT),'-t',str(profile['threads']),'-ngl',str(profile['gpu_layers']),'--ctx-size',str(profile['context'])]
    mode=profile['load_mode']
    if '--load-mode' in help_text:args += ['--load-mode',mode]
    elif mode=='none':
        if '--no-mmap' not in help_text:raise RuntimeError('This llama-server cannot honor load mode none')
        args += ['--no-mmap']
    elif mode in ('mlock','mmap+mlock'):
        if '--mlock' not in help_text:raise RuntimeError('This llama-server does not support mlock')
        args += ['--mlock']
    elif mode=='dio':raise RuntimeError('This llama-server does not support direct-I/O load mode')
    return args


class ProfiledRuntimeState(bridge.RuntimeState):
    def __init__(self):
        super().__init__()
        self.profile=normalize_profile({})

    def snapshot(self):
        result=super().snapshot()
        result.update(profile=dict(self.profile),cpu_threads=self.profile['threads'],gpu_layers=self.profile['gpu_layers'],load_mode=self.profile['load_mode'])
        return result

    def start_drive(self,spec,model_name,relative_path,access_token):
        profile=load_profile(model_name,relative_path)
        if not bridge.resolve_llama_server():raise FileNotFoundError('llama-server is missing; no model download was started')
        self.profile=profile
        return super().start_drive(spec,model_name,relative_path,access_token)

    def _launch_cached(self,job_id,model_path,model_name,relative_path):
        if model_path.suffix.lower()!='.gguf':raise ValueError('Chat requires a GGUF')
        info=bridge.inspect_gguf(model_path)
        executable=bridge.resolve_llama_server()
        if not executable:raise FileNotFoundError('llama-server is missing')
        profile=load_profile(model_name,relative_path)
        args=command(executable,model_path,profile,server_help(executable))
        with self.lock:
            if self.job_id!=job_id:return
            self.profile=profile
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
        with self.lock:
            if self.job_id!=job_id:
                process.terminate();process.wait(5);return
            self.process=process;self.started_at=time.time();self.ready_at=None;self.phase='loading';self.last_error=None;self.exit_code=None
        self.append_log(f"Verified GGUF v{info['version']}; context={profile['context']}, threads={profile['threads']}, GPU layers={profile['gpu_layers']}")
        threading.Thread(target=self._read_output,args=(process,),daemon=True).start()
        threading.Thread(target=self._monitor_ready,args=(process,),daemon=True).start()
