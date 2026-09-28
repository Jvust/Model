from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .hardware import disk_status
except ImportError:
    from hardware import disk_status


_DRIVE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
_CHUNK_BYTES = 8 * 1024 * 1024


def default_cache_root() -> Path:
    configured = os.environ.get("MODEL_CACHE_ROOT", "").strip()
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured))).resolve()

    if os.name == "nt":
        return Path(r"D:\Model").resolve()

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (Path(local_app_data) / "JvustModel" / "cache").resolve()

    return (Path.home() / ".cache" / "JvustModel" / "models").resolve()


def validate_drive_file_id(file_id: str) -> str:
    value = str(file_id or "").strip()
    if not _DRIVE_FILE_ID_RE.fullmatch(value):
        raise ValueError("Invalid Google Drive file ID.")
    return value


def safe_extension(name: str) -> str:
    suffix = Path(str(name or "")).suffix.lower()
    if not suffix or len(suffix) > 12 or not re.fullmatch(r"\.[a-z0-9]+", suffix):
        return ".bin"
    return suffix


def cache_key(file_id: str) -> str:
    value = validate_drive_file_id(file_id)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class DriveFileSpec:
    file_id: str
    name: str
    size: int | None = None
    md5_checksum: str | None = None
    resource_key: str | None = None
    modified_time: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "DriveFileSpec":
        file_id = validate_drive_file_id(str(payload.get("drive_file_id") or ""))
        name = str(payload.get("file_name") or payload.get("name") or "model.bin").strip() or "model.bin"

        size_raw = payload.get("size")
        size = None
        if size_raw not in (None, "", 0, "0"):
            try:
                if isinstance(size_raw, bool) or (not isinstance(size_raw, int) and not (isinstance(size_raw, str) and size_raw.isdecimal())):
                    raise ValueError("Model size must be an exact integer")
                size = int(size_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("Model size must be an integer.") from exc
            if size <= 0:
                raise ValueError("Model size must be positive.")

        md5 = str(payload.get("md5_checksum") or payload.get("md5Checksum") or "").strip() or None
        if md5 and not re.fullmatch(r"[0-9a-fA-F]{32}", md5):
            raise ValueError("Invalid Drive MD5 checksum")
        resource_key = str(payload.get("resource_key") or payload.get("resourceKey") or "").strip() or None

        return cls(
            file_id=file_id,
            name=name,
            size=size,
            md5_checksum=md5,
            resource_key=resource_key,
            modified_time=str(payload.get("modified_time") or payload.get("modifiedTime") or "") or None,
        )


class DriveCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_cache_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def access_token(self, payload: dict, provider: Callable[[], str]) -> str:
        """Resolve upstream credentials; desktop cache overrides only scanned local IDs."""
        return provider()

    def _paths(self, spec: DriveFileSpec) -> tuple[Path, Path, Path]:
        stem = cache_key(spec.file_id)
        suffix = safe_extension(spec.name)
        final = self.root / f"{stem}{suffix}"
        partial = self.root / f"{stem}{suffix}.part"
        metadata = self.root / f"{stem}.json"
        return final, partial, metadata

    def metadata(self, spec: DriveFileSpec) -> dict | None:
        final, _, metadata = self._paths(spec)
        if final.is_symlink() or metadata.is_symlink() or not final.is_file() or not metadata.is_file():
            return None

        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if data.get("file_id") != spec.file_id:
            return None
        if spec.size and final.stat().st_size != spec.size:
            return None
        if data.get("size") and final.stat().st_size != int(data["size"]):
            return None
        if data.get("status") not in (None, "complete"):
            return None
        if spec.modified_time and data.get("modified_time") != spec.modified_time:
            return None
        if spec.md5_checksum and (data.get("md5_checksum") != spec.md5_checksum.lower() or data.get("verified_md5") != spec.md5_checksum.lower()):
            return None
        # Old checksum-free records are re-verified locally on next use.
        if not data.get("sha256") or data.get("verified_mtime_ns") != final.stat().st_mtime_ns:
            return None
        return data

    def cached_path(self, spec: DriveFileSpec) -> Path | None:
        final, _, _ = self._paths(spec)
        return final if self.metadata(spec) else None

    def describe(self, spec: DriveFileSpec) -> dict:
        final, partial, _ = self._paths(spec)
        cached = self.cached_path(spec)
        return {
            "cached": cached is not None,
            "cached_bytes": cached.stat().st_size if cached else 0,
            "partial_bytes": partial.stat().st_size if partial.is_file() and not partial.is_symlink() else 0,
            "candidate_bytes": final.stat().st_size if final.is_file() and not final.is_symlink() and spec.size == final.stat().st_size else 0,
            "expected_bytes": spec.size,
            "cache_file": final.name,
        }

    def list_entries(self) -> list[dict]:
        """Return persistent cache records without exposing local filesystem paths."""
        entries = []
        with self._lock:
            for metadata_path in sorted(self.root.glob("*.json")):
                try:
                    data = json.loads(metadata_path.read_text(encoding="utf-8"))
                    file_id = validate_drive_file_id(str(data.get("file_id") or ""))
                    name = str(data.get("name") or "model.bin")
                    final, partial, _ = self._paths(
                        DriveFileSpec(
                            file_id=file_id,
                            name=name,
                            size=int(data.get("size") or 0) or None,
                            md5_checksum=data.get("md5_checksum"),
                        )
                    )
                    entries.append(
                        {
                            "file_id": file_id,
                            "name": name,
                            "size": final.stat().st_size if final.exists() else 0,
                            "expected_size": data.get("size"),
                            "cached": bool(final.is_file() and data.get("status") in (None, "complete") and data.get("verified_mtime_ns") == final.stat().st_mtime_ns),
                            "partial": partial.exists(),
                            "updated_at": metadata_path.stat().st_mtime,
                        }
                    )
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
        return entries

    def delete_file_id(self, file_id: str) -> dict:
        """Delete one persistent cache record and any resumable partial file."""
        target = validate_drive_file_id(file_id)
        deleted = 0
        removed = False
        with self._lock:
            for metadata_path in list(self.root.glob("*.json")):
                try:
                    data = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if str(data.get("file_id") or "") != target:
                    continue

                name = str(data.get("name") or "model.bin")
                spec = DriveFileSpec(
                    file_id=target,
                    name=name,
                    size=int(data.get("size") or 0) or None,
                    md5_checksum=data.get("md5_checksum"),
                )
                final, partial, metadata = self._paths(spec)
                for candidate in (final, partial, metadata):
                    try:
                        if candidate.exists():
                            if candidate.is_file():
                                deleted += candidate.stat().st_size
                            candidate.unlink()
                            removed = True
                    except OSError:
                        pass
        return {"file_id": target, "removed": removed, "deleted_bytes": deleted}

    def delete_all(self) -> dict:
        deleted = 0
        removed = 0
        for entry in self.list_entries():
            result = self.delete_file_id(entry["file_id"])
            if result["removed"]:
                removed += 1
                deleted += int(result["deleted_bytes"] or 0)
        return {"removed": removed, "deleted_bytes": deleted}

    def download_preflight(self, spec: DriveFileSpec, reserve_bytes: int = 2 * 1024**3) -> dict:
        _, partial, _ = self._paths(spec)
        partial_bytes = partial.stat().st_size if partial.exists() else 0
        expected = int(spec.size or 0)
        remaining = max(0, expected - partial_bytes) if expected else None
        disk = disk_status(self.root)
        required = (remaining + int(reserve_bytes)) if remaining is not None else int(reserve_bytes)
        ok = int(disk["free_bytes"]) >= required
        return {
            "ok": ok,
            "disk": disk,
            "expected_bytes": spec.size,
            "partial_bytes": partial_bytes,
            "remaining_bytes": remaining,
            "reserve_bytes": int(reserve_bytes),
            "required_bytes": required,
        }

    @staticmethod
    def _write_metadata(path, value):
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    @staticmethod
    def _hashes(path, progress=None, total=None):
        sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
        count = 0
        with path.open("rb") as handle:
            while block := handle.read(_CHUNK_BYTES):
                sha.update(block); md5.update(block); count += len(block)
                if progress:
                    progress(count, total or path.stat().st_size)
        return sha.hexdigest(), md5.hexdigest()

    def _complete_record(self, spec, path, sha, actual_md5):
        return {"file_id": spec.file_id, "name": spec.name, "size": path.stat().st_size,
                "status": "complete", "md5_checksum": spec.md5_checksum.lower() if spec.md5_checksum else None,
                "verified_md5": actual_md5 if spec.md5_checksum else None,
                "sha256": sha, "modified_time": spec.modified_time,
                "verified_mtime_ns": path.stat().st_mtime_ns,
                "source_checksum_verified": bool(spec.md5_checksum)}

    def download(self, spec: DriveFileSpec, access_token: str,
                 progress: Callable[[int, int | None], None] | None = None) -> Path:
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("Google Drive access token is missing.")
        if spec.md5_checksum and not re.fullmatch('[a-fA-F0-9]{32}', spec.md5_checksum):
            raise ValueError("Invalid Drive MD5 checksum")
        with self._lock:
            cached = self.cached_path(spec)
            if cached:
                if progress:
                    progress(cached.stat().st_size, spec.size or cached.stat().st_size)
                return cached
            final, partial, metadata = self._paths(spec)
            if any(path.is_symlink() for path in (final, partial, metadata)):
                raise ValueError("Symlink cache files are not accepted")
            # Upgrade a legacy complete cache without re-downloading it, but only
            # when the current source checksum establishes content identity.
            if final.is_file() and spec.size == final.stat().st_size and spec.md5_checksum:
                sha, md5 = self._hashes(final, progress, spec.size)
                if md5 == spec.md5_checksum.lower():
                    self._write_metadata(metadata, self._complete_record(spec, final, sha, md5))
                    return final
            signature = hashlib.sha256(json.dumps([spec.file_id, spec.name, spec.size,
                spec.md5_checksum, spec.modified_time], ensure_ascii=False).encode()).hexdigest()
            prior = {}
            if metadata.is_file():
                try:
                    prior = json.loads(metadata.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    pass
            offset = partial.stat().st_size if partial.is_file() else 0
            # A partial is resumable only when it is bound to an unchanged source.
            if offset and (prior.get("partial_signature") != signature or not spec.md5_checksum or (spec.size and offset > spec.size)):
                import uuid
                partial.rename(partial.with_name(partial.name + ".stale-" + uuid.uuid4().hex))
                offset = 0
            preflight = self.download_preflight(spec)
            if not preflight["ok"]:
                raise RuntimeError("本机缓存空间不足，权重和临时文件均已保留")
            self._write_metadata(metadata, {"file_id": spec.file_id, "name": spec.name, "size": spec.size,
                "status": "downloading", "partial_signature": signature,
                "md5_checksum": spec.md5_checksum, "modified_time": spec.modified_time})
            if not (spec.size and offset == spec.size):
                url = "https://www.googleapis.com/drive/v3/files/" + spec.file_id + "?alt=media&supportsAllDrives=true"
                headers = {"Authorization": "Bearer " + token, "Accept-Encoding": "identity"}
                if spec.resource_key:
                    headers["X-Goog-Drive-Resource-Keys"] = spec.file_id + "/" + spec.resource_key
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                try:
                    response = urlopen(Request(url, headers=headers, method="GET"), timeout=60)
                except HTTPError as error:
                    if error.code in (401,403):
                        raise PermissionError("Drive 登录已过期或文件权限不足；重新连接后接续，已保存分片保留") from error
                    if error.code == 404:
                        raise FileNotFoundError("Google Drive model file was not found") from error
                    raise RuntimeError(f"Drive download HTTP {error.code}") from error
                except (URLError, TimeoutError, OSError) as error:
                    raise RuntimeError("Drive 网络中断；重新连接后可接续") from error
                with response:
                    status = int(getattr(response, "status", 200))
                    if offset and status == 206:
                        content_range = getattr(response, "headers", {}).get("Content-Range", "")
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                        if not match or int(match[1]) != offset or int(match[2]) < offset or (spec.size and int(match[3]) != spec.size):
                            raise ValueError("Invalid Drive Content-Range; partial bytes retained")
                    elif status == 200:
                        offset = 0
                    else:
                        raise ValueError("Unexpected Drive response status")
                    downloaded = offset
                    with partial.open("ab" if offset else "wb") as handle:
                        while chunk := response.read(_CHUNK_BYTES):
                            downloaded += len(chunk)
                            if spec.size and downloaded > spec.size:
                                raise ValueError("Drive returned more bytes than the selected file version")
                            handle.write(chunk)
                            if progress:
                                progress(downloaded, spec.size)
                        handle.flush(); os.fsync(handle.fileno())
            actual = partial.stat().st_size
            if spec.size and actual != spec.size:
                raise RuntimeError(f"Drive download incomplete: expected {spec.size} bytes, got {actual}")
            sha, actual_md5 = self._hashes(partial, progress, spec.size)
            if spec.md5_checksum and actual_md5 != spec.md5_checksum.lower():
                import uuid
                partial.rename(partial.with_name(partial.name + ".bad-checksum-" + uuid.uuid4().hex))
                raise ValueError("Drive MD5 mismatch; failed partial quarantined, not marked complete")
            partial.replace(final)
            self._write_metadata(metadata, self._complete_record(spec, final, sha, actual_md5))
            return final
