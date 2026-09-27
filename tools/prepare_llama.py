"""Bundle the exact official CPU runtime used by acceptance tests; no model weights."""
from pathlib import Path
from urllib.request import Request, urlopen
import hashlib
import json
import zipfile

TAG = 'b11146'
NAME = f'llama-{TAG}-bin-win-cpu-x64.zip'
COMMIT = '7fe450e19305b828c199d602c23a8337aaa1f03b'


def prepare(destination=None):
    root=Path(__file__).resolve().parents[1]
    target=Path(destination or root/'runtime'/'llama')
    target.mkdir(parents=True,exist_ok=True)
    with urlopen(Request(f'https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{TAG}',headers={'User-Agent':'Model-release-builder'}),timeout=30) as response:
        release=json.load(response)
    asset=next(item for item in release['assets'] if item['name']==NAME)
    expected=asset.get('digest','')
    if not expected.startswith('sha256:'):raise RuntimeError('Official release asset has no SHA256; refusing to bundle')
    archive=target/'llama.zip'
    digest=hashlib.sha256()
    with urlopen(asset['browser_download_url'],timeout=60) as source,archive.open('wb') as output:
        total=0
        while block:=source.read(1024*1024):
            total+=len(block)
            if total>300*1024**2:raise RuntimeError('CPU runtime archive unexpectedly large')
            digest.update(block);output.write(block)
    if digest.hexdigest()!=expected.split(':',1)[1]:raise RuntimeError('llama.cpp archive checksum mismatch')
    if archive.stat().st_size!=asset['size']:raise RuntimeError('llama.cpp archive size mismatch')
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            if not (target/item.filename).resolve().is_relative_to(target.resolve()):raise RuntimeError('Unsafe archive path')
        zipped.extractall(target)
    archive.unlink()
    servers=list(target.rglob('llama-server.exe'))
    if len(servers)!=1:raise RuntimeError('Expected one llama-server executable')
    with urlopen(f'https://raw.githubusercontent.com/ggml-org/llama.cpp/{COMMIT}/LICENSE',timeout=30) as source:
        (target/'LICENSE.llama.cpp').write_bytes(source.read())
    record={'release':TAG,'source_commit':COMMIT,'asset':NAME,'asset_sha256':expected.split(':',1)[1],'executable':str(servers[0].relative_to(target)).replace('\\','/'),'runtime_only_no_weights':True}
    (target/'BUNDLE.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record))
    return servers[0].resolve()


if __name__=='__main__':prepare()
