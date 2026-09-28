"""Actual Windows EXE smoke gate, never called a GPU/model inference test."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

def main():
    if os.name != 'nt': raise RuntimeError('Run this EXE gate on Windows')
    with tempfile.TemporaryDirectory() as temporary:
        env=os.environ.copy();env.update(MODEL_CACHE_ROOT=temporary,MODEL_DESKTOP_CONFIG=str(Path(temporary)/'source.json'),MODEL_BRIDGE_PORT='8765',LLAMA_SERVER_PATH='missing.exe')
        with (Path(temporary)/'runtime.log').open('wb') as log:
            process=subprocess.Popen(['runtime/ModelRuntime.exe'],env=env,stdout=log,stderr=log)
            try:
                for _ in range(120):
                    if process.poll() is not None:raise RuntimeError('EXE exited before health check')
                    try:
                        with urlopen('http://127.0.0.1:8765/health',timeout=1) as res:health=json.load(res)
                        break
                    except OSError:time.sleep(.25)
                else:raise TimeoutError('EXE health timed out')
                if health.get('version')!=19:raise RuntimeError('Wrong executable version')
                with urlopen('http://127.0.0.1:8765/',timeout=5) as res:page=res.read()
                if b'activation.js' not in page or b'desktop-source.js' not in page:raise RuntimeError('Embedded website incomplete')
                for path in ['/v1/activation/history','/v1/activation/status','/v1/desktop/status','/v1/native/catalog','/v1/runtime']:
                    with urlopen('http://127.0.0.1:8765'+path,timeout=10) as res:json.load(res)
                report={'version':19,'executable_smoke':'PASS','gpu_inference_tested':False,'actual_drivefs_tested':False}
                Path('qa-evidence').mkdir(exist_ok=True)
                Path('qa-evidence/windows-candidate-exe.json').write_text(json.dumps(report,indent=2))
            finally:
                process.terminate()
                try:process.wait(10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
if __name__=='__main__':main()
