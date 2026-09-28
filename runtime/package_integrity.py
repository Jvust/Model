"""Bounded, metadata-first checks for model packages (no torch imports).

A completion marker or aggregate byte count is NOT proof of a complete model.
Every shard named in each checkpoint index must exist, be non-empty and match
its advertised size. Safetensors headers are checked without reading tensors.
"""
from __future__ import annotations
import json
import re
import struct
from pathlib import Path, PurePosixPath

MAX_JSON = 20 * 1024**2
MAX_HEADER = 32 * 1024**2
RESERVED = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)
FORBIDDEN = {'.py', '.pyc', '.pyd', '.exe', '.dll', '.ps1', '.bat', '.cmd', '.pkl', '.pth'}


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError('Invalid package-relative path')
    if '\\' in value or ':' in value or '\x00' in value or value.startswith('/'):
        raise ValueError('Absolute/Windows paths are not model-relative paths')
    parts = value.split('/')
    if any(p in {'', '.', '..'} or p.endswith((' ', '.')) or RESERVED.match(p) for p in parts):
        raise ValueError('Unsafe package-relative path: ' + value)
    return value


def read_json(path: Path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_JSON:
        raise ValueError('Missing, unsafe or oversized JSON: ' + path.name)
    with path.open('r', encoding='utf-8-sig') as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError('Model metadata must be a JSON object: ' + path.name)
    return value


def shard_references(index_path: str, document: dict) -> set[str]:
    safe_relative(index_path)
    mapping = document.get('weight_map')
    if not isinstance(mapping, dict) or not mapping or len(mapping) > 200000:
        raise ValueError('Checkpoint index requires a non-empty bounded weight_map')
    parent = PurePosixPath(index_path).parent
    result = set()
    for name in mapping.values():
        safe_relative(name)
        if not name.endswith(('.safetensors', '.bin')):
            raise ValueError('Unsupported checkpoint shard extension')
        result.add(str(parent / name))
    return result


def check_index_manifest(index_path: str, document: dict, paths: set[str]) -> set[str]:
    needed = shard_references(index_path, document)
    missing = needed - paths
    if missing:
        raise ValueError('Missing checkpoint shards in source manifest: ' + ', '.join(sorted(missing)))
    return needed


def check_safetensors(path: Path) -> dict:
    """Validate header, bounds and file length, not tensor contents/checksums."""
    size = path.stat().st_size
    with path.open('rb') as handle:
        raw = handle.read(8)
        if len(raw) != 8:
            raise ValueError('Truncated safetensors: ' + path.name)
        length = struct.unpack('<Q', raw)[0]
        if not 2 <= length <= MAX_HEADER or 8 + length > size:
            raise ValueError('Invalid safetensors header length: ' + path.name)
        header = json.loads(handle.read(length))
    if not isinstance(header, dict):
        raise ValueError('Invalid safetensors header: ' + path.name)
    spans = []
    for name, tensor in header.items():
        if name == '__metadata__':
            continue
        if not isinstance(tensor, dict):
            raise ValueError('Invalid tensor record')
        offsets = tensor.get('data_offsets')
        if not isinstance(offsets, list) or len(offsets) != 2 or any(type(n) is not int for n in offsets):
            raise ValueError('Invalid tensor offsets')
        lo, hi = offsets
        if not 0 <= lo <= hi <= size - 8 - length:
            raise ValueError('Truncated tensor data: ' + path.name)
        spans.append((lo, hi))
    if not spans:
        raise ValueError('Safetensors file has no tensors')
    previous = 0
    for lo, hi in sorted(spans):
        if lo != previous:
            raise ValueError('Non-contiguous/overlapping safetensors data')
        previous = hi
    if previous != size - 8 - length:
        raise ValueError('Safetensors trailing or missing bytes')
    return {'tensor_count': len(spans), 'header_bytes': length, 'size': size}


def verify_directory(directory, expected_sizes=None, header_check=True) -> dict:
    root = Path(directory).resolve(strict=True)
    expected = dict(expected_sizes or {})
    paths = []
    for relative, size in expected.items():
        safe_relative(relative)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError('Invalid advertised file size')
        file = root / relative
        if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root):
            raise ValueError('Missing or unsafe model file: ' + relative)
        if file.stat().st_size != size:
            raise ValueError('Model file size mismatch: ' + relative)
    for file in root.rglob('*'):
        if file.is_symlink():
            raise ValueError('Symlinks are not allowed inside materialized packages')
        if file.is_file():
            relative = file.relative_to(root).as_posix()
            safe_relative(relative)
            paths.append(relative)
    referenced = set()
    for relative in paths:
        if relative.endswith('.index.json'):
            referenced |= check_index_manifest(relative, read_json(root / relative), set(paths))
    for relative in referenced:
        if (root / relative).stat().st_size == 0:
            raise ValueError('Empty checkpoint shard: ' + relative)
    safe_count = 0
    if header_check:
        for relative in paths:
            if relative.endswith('.safetensors'):
                check_safetensors(root / relative)
                safe_count += 1
    return {'status': 'structure_verified', 'file_count': len(paths), 'indexed_shards': len(referenced),
            'safetensors_headers': safe_count, 'tensor_checksums_verified': False,
            'model_execution_verified': False}
