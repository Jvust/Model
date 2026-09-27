"""Non-authenticated deployment probe. Never follows OAuth redirects or redeems a code."""
import json
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode
from urllib.error import HTTPError

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

opener=build_opener(NoRedirect)
base='https://drive-oauth-bridge.143322378jb.workers.dev'
result={'provider':base,'consent_performed':False,'drive_access_tested':False,'worker_v2_deployed':False,'checks':[]}
try:
    with opener.open(Request(base+'/',headers={'User-Agent':'Model-readonly-release-probe'}),timeout=15) as response:
        raw=response.read(2048).decode('utf-8',errors='replace')
    try:result['worker_v2_deployed']=json.loads(raw).get('version')==2
    except (ValueError,TypeError):pass
    for target in ['https://jvust.github.io/Model/','http://127.0.0.1:8765/']:
        try:
            response=opener.open(base+'/auth?'+urlencode({'return_to':target}),timeout=15)
            status=response.status;response.close()
        except HTTPError as error:status=error.code;error.close()
        result['checks'].append({'return_to':target,'http_status':status,'redirect_accepted':status==302})
except Exception as error:result['probe_error']=type(error).__name__
path=Path('qa-evidence');path.mkdir(exist_ok=True)
(path/'oauth-public-probe.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
