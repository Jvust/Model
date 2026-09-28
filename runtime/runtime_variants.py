"""Explicit, curated runtime variants. Original Drive models stay read-only.

Only a user-confirmed profile can fetch these fixed HF repositories. Resolve
immutable revisions and sizes/SHA256 before downloading, resume .part files,
verify streaming SHA256 before promotion. No arbitrary URL or remote code.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

PROFILES = {
    'qwen2511_gguf_q4km': {
        'label': 'Qwen-Image-Edit-2511 · GGUF Q4_K_M + FP8 encoder',
        'adapter': 'qwen_image_edit_2511_gguf',
        'note': '单独下载量化运行副本，不使用/替换 Drive 原始 BF16 主权重；实机推理待验收',
        'files': [
            ('unet', 'unsloth/Qwen-Image-Edit-2511-GGUF', 'qwen-image-edit-2511-Q4_K_M.gguf'),
            ('clip', 'Comfy-Org/HunyuanVideo_1.5_repackaged', 'split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors'),
            ('vae', 'Comfy-Org/Qwen-Image_ComfyUI', 'split_files/vae/qwen_image_vae.safetensors'),
        ],
    },
}
LOCK = threading.Lock()


def cancelled(event):
    if event.is_set():
        raise InterruptedError('Task cancelled; partial downloads retained')


def file_hash(path, cancel=None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(8 * 1024**2):
            if cancel is not None:
                cancelled(cancel)
            value.update(chunk)
    return value.hexdigest()


class RuntimeVariants:
    def __init__(self, cache_root):
        self.root = Path(cache_root) / 'runtime-variants'
        self.root.mkdir(parents=True, exist_ok=True)

    def manifest(self, profile):
        if profile not in PROFILES:
            raise ValueError('Unknown runtime profile')
        folder = self.root / profile
        if self.root.is_symlink() or folder.is_symlink():
            raise ValueError('Symlink runtime variant roots are not accepted')
        path = folder / 'manifest.json'
        if path.is_symlink():
            raise ValueError('Symlink manifests are not accepted')
        if path.is_file():
            data = json.loads(path.read_text(encoding='utf-8'))
            self.validate_manifest(profile, data)
            return data
        return None

    @staticmethod
    def validate_manifest(profile, data):
        if data.get('profile') != profile or data.get('schema_version') != 1:
            raise ValueError('Runtime profile identity mismatch')
        actual = data.get('files')
        expected = PROFILES[profile]['files']
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError('Runtime profile files mismatch')
        for item, (role, repo, filename) in zip(actual, expected):
            if (item.get('role'), item.get('repo'), item.get('filename'), item.get('name')) != (role, repo, filename, filename.rsplit('/', 1)[-1]):
                raise ValueError('Untrusted runtime variant path/repository')
            if not re.fullmatch('[a-f0-9]{40}', item.get('revision', '')) or not re.fullmatch('[a-f0-9]{64}', item.get('sha256', '')) or type(item.get('size')) is not int or item['size'] <= 0:
                raise ValueError('Invalid runtime variant integrity metadata')
        if data.get('total_bytes') != sum(f['size'] for f in actual):
            raise ValueError('Invalid runtime variant total')

    def resolve(self, profile, cancel):
        saved = self.manifest(profile)
        if saved:
            return saved
        files = []
        for role, repo, filename in PROFILES[profile]['files']:
            cancelled(cancel)
            request = Request('https://huggingface.co/api/models/' + repo + '?blobs=true', headers={'User-Agent': 'Model/0.19'})
            with urlopen(request, timeout=40) as response:
                raw = response.read(8 * 1024**2 + 1)
            if len(raw) > 8 * 1024**2:
                raise ValueError('HF metadata is too large')
            info = json.loads(raw)
            revision = info.get('sha', '')
            if not re.fullmatch('[a-f0-9]{40}', revision):
                raise ValueError('HF did not supply an immutable revision')
            item = next((f for f in info['siblings'] if f['rfilename'] == filename), None)
            lfs = (item or {}).get('lfs') or {}
            sha = lfs.get('sha256', '')
            size = (item or {}).get('size') or lfs.get('size')
            if not re.fullmatch('[a-f0-9]{64}', sha) or type(size) is not int or size <= 0:
                raise ValueError('Missing trusted size/SHA256 for ' + filename)
            files.append(dict(role=role, repo=repo, filename=filename, revision=revision, sha256=sha, size=size,
                              name=filename.rsplit('/', 1)[-1]))
        data = dict(profile=profile, files=files, total_bytes=sum(f['size'] for f in files), schema_version=1)
        folder = self.root / profile
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / ('manifest.' + uuid.uuid4().hex + '.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(folder / 'manifest.json')
        return data

    def prepare(self, profile, cancel, progress):
        if profile not in PROFILES:
            raise ValueError('Unknown runtime profile')
        while not LOCK.acquire(timeout=0.2):
            cancelled(cancel)
        try:
            data = self.resolve(profile, cancel)
            files = {}
            folder = self.root / profile
            for item in data['files']:
                cancelled(cancel)
                destination = folder / item['name']
                partial = destination.with_suffix(destination.suffix + '.part')
                stamp = destination.with_suffix(destination.suffix + '.verified.json')
                if any(p.is_symlink() for p in (folder, destination, partial, stamp)):
                    raise ValueError('Symlink runtime variant paths are not accepted')
                if destination.is_file():
                    evidence = json.loads(stamp.read_text()) if stamp.is_file() else {}
                    if (destination.stat().st_size == item['size'] and evidence.get('sha256') == item['sha256']
                            and evidence.get('mtime_ns') == destination.stat().st_mtime_ns):
                        files[item['role']] = destination
                        progress('复用已校验量化权重：' + item['name'])
                        continue
                    if destination.stat().st_size == item['size'] and file_hash(destination, cancel) == item['sha256']:
                        files[item['role']] = destination
                        self._stamp(stamp, destination, item)
                        continue
                    raise ValueError('Existing variant failed SHA256: ' + item['name'] + '; original file retained')
                if destination.is_symlink() or partial.is_symlink() or stamp.is_symlink():
                    raise ValueError('Symlink runtime variant paths are not accepted')
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > item['size']:
                    raise ValueError('Oversized partial file; manual inspection required')
                if shutil.disk_usage(folder).free < item['size'] - offset + 2 * 1024**3:
                    raise ValueError('Insufficient free runtime-cache disk')
                if offset < item['size']:
                    url = 'https://huggingface.co/' + item['repo'] + '/resolve/' + item['revision'] + '/' + quote(item['filename'], safe='/')
                    headers = {'User-Agent': 'Model/0.19', 'Accept-Encoding': 'identity'}
                    if offset:
                        headers['Range'] = f'bytes={offset}-'
                    with urlopen(Request(url, headers=headers), timeout=60) as response:
                        status = response.status
                        if offset and status == 206:
                            content_range = response.headers.get('Content-Range', '')
                            if not content_range.startswith(f'bytes {offset}-') or not content_range.endswith('/' + str(item['size'])):
                                raise ValueError('Invalid range response')
                        elif status == 200:
                            offset = 0
                        else:
                            raise ValueError('Unexpected download HTTP status')
                        with partial.open('ab' if offset else 'wb') as stream:
                            while chunk := response.read(8 * 1024**2):
                                cancelled(cancel)
                                offset += len(chunk)
                                if offset > item['size']:
                                    raise ValueError('Download exceeds declared size')
                                stream.write(chunk)
                                progress(f'{item["name"]} · {offset}/{item["size"]} bytes')
                            stream.flush()
                            os.fsync(stream.fileno())
                progress('校验 SHA256：' + item['name'])
                if partial.stat().st_size != item['size'] or file_hash(partial, cancel) != item['sha256']:
                    raise ValueError('Runtime variant size/SHA256 mismatch; partial retained')
                cancelled(cancel)
                partial.replace(destination)
                self._stamp(stamp, destination, item)
                files[item['role']] = destination
            return files
        finally:
            LOCK.release()

    @staticmethod
    def _stamp(path, file, item):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'sha256': item['sha256'], 'size': item['size'], 'mtime_ns': file.stat().st_mtime_ns}), encoding='utf-8')
        temporary.replace(path)
