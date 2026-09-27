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

    @classmethod
    def from_payload(cls, payload: dict) -> "DriveFileSpec":
        file_id = validate_drive_file_id(str(payload.get("drive_file_id") or ""))
        name = str(payload.get("file_name") or payload.get("name") or "model.bin").strip() or "model.bin"

        size_raw = payload.get("size")
        size = None
        if size_raw not in (None, "", 0, "0"):
            try:
                size = int(size_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("Model size must be an integer.") from exc
            if size <= 0:
                raise ValueError("Model size must be positive.")

        md5 = str(payload.get("md5_checksum") or payload.get("md5Checksum") or "").strip() or None
        resource_key = str(payload.get("resource_key") or payload.get("resourceKey") or "").strip() or None

        return cls(
            file_id=file_id,
            name=name,
            size=size,
            md5_checksum=md5,
            resource_key=resource_key,
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
        if not final.exists() or not metadata.exists():
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
        if spec.md5_checksum and data.get("md5_checksum") not in (None, spec.md5_checksum):
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
            "partial_bytes": partial.stat().st_size if partial.exists() else 0,
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
                            "cached": final.exists(),
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

    def download(
        self,
        spec: DriveFileSpec,
        access_token: str,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("Google Drive access token is missing.")

        with self._lock:
            cached = self.cached_path(spec)
            if cached:
                if progress:
                    progress(cached.stat().st_size, spec.size or cached.stat().st_size)
                return cached

            final, partial, metadata = self._paths(spec)
            partial.parent.mkdir(parents=True, exist_ok=True)

            offset = partial.stat().st_size if partial.exists() else 0
            if spec.size and offset > spec.size:
                partial.unlink(missing_ok=True)
                offset = 0

            preflight = self.download_preflight(spec)
            if not preflight["ok"]:
                disk = preflight["disk"]
                required = int(preflight["required_bytes"])
                raise RuntimeError(
                    "本机缓存空间不足："
                    f"下载和安全余量共需约 {required / (1024**3):.1f} GB，"
                    f"当前可用约 {disk['free_gb']:.1f} GB。"
                )

            url = (
                "https://www.googleapis.com/drive/v3/files/"
                + spec.file_id
                + "?alt=media&supportsAllDrives=true"
            )
            headers = {"Authorization": "Bearer " + token}
            if spec.resource_key:
                headers["X-Goog-Drive-Resource-Keys"] = (
                    spec.file_id + "/" + spec.resource_key
                )
            if offset:
                headers["Range"] = f"bytes={offset}-"

            request = Request(url, headers=headers, method="GET")

            try:
                response = urlopen(request, timeout=60)
            except HTTPError as exc:
                if exc.code == 401:
                    raise PermissionError("Google Drive access token expired or is invalid.") from exc
                if exc.code == 403:
                    raise PermissionError("Google Drive denied access to this model file.") from exc
                if exc.code == 404:
                    raise FileNotFoundError("Google Drive model file was not found.") from exc
                raise RuntimeError(f"Google Drive download failed: HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise RuntimeError("Could not reach Google Drive: " + str(exc)) from exc

            with response:
                status = int(getattr(response, "status", 200))
                if offset and status != 206:
                    partial.unlink(missing_ok=True)
                    offset = 0

                mode = "ab" if offset and status == 206 else "wb"
                downloaded = offset if mode == "ab" else 0

                with partial.open(mode) as handle:
                    while True:
                        chunk = response.read(_CHUNK_BYTES)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress(downloaded, spec.size)

            actual = partial.stat().st_size
            if spec.size and actual != spec.size:
                raise RuntimeError(
                    f"Drive download incomplete: expected {spec.size} bytes, got {actual}."
                )

            partial.replace(final)
            metadata.write_text(
                json.dumps(
                    {
                        "file_id": spec.file_id,
                        "name": spec.name,
                        "size": final.stat().st_size,
                        "md5_checksum": spec.md5_checksum,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return final
