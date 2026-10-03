from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Callable

try:
    from .drive_cache import DriveCache, DriveFileSpec, default_cache_root
except ImportError:
    from drive_cache import DriveCache, DriveFileSpec, default_cache_root


PACKAGE_ROOT = Path(
    os.environ.get(
        "MODEL_PACKAGE_ROOT",
        str(default_cache_root() / "packages"),
    )
).expanduser().resolve()

_COPY_FALLBACK_LIMIT = int(
    os.environ.get("MODEL_PACKAGE_COPY_FALLBACK_BYTES", str(64 * 1024 * 1024))
)


def _safe_package_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise ValueError("Invalid package_path.")
    reserved = {"con", "prn", "aux", "nul"} | {f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10)}
    if any(part.endswith((".", " ")) or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
           or part.split(".")[0].casefold() in reserved for part in path.parts):
        raise ValueError("Invalid package_path component.")
    return "/".join(path.parts)


def _safe_relative_path(value: str, package_path: str) -> str:
    raw = _safe_package_path(value)
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError("Invalid manifest relative_path.")
    normalized = "/".join(path.parts)
    prefix = package_path.rstrip("/")
    if normalized == prefix:
        raise ValueError("Manifest entry points to package directory, not a file.")
    if normalized.startswith(prefix + "/"):
        normalized = normalized[len(prefix) + 1 :]
    local = PurePosixPath(normalized)
    if not local.parts or ".." in local.parts or any(":" in p for p in local.parts) or any(p.casefold().startswith(".jvust-package.json") for p in local.parts):
        raise ValueError("Invalid package-local path.")
    return "/".join(local.parts)


def package_cache_key(package_path: str) -> str:
    normalized = _safe_package_path(package_path)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    leaf = PurePosixPath(normalized).name
    safe_leaf = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in leaf
    )[:80] or "package"
    return f"{safe_leaf}-{digest}"


def normalize_manifest(payload: dict) -> tuple[str, list[dict]]:
    package_path = _safe_package_path(str(payload.get("package_path") or ""))
    raw_files = payload.get("manifest_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("manifest_files must be a non-empty array.")
    if len(raw_files) > 10000:
        raise ValueError("Package manifest exceeds 10000 files.")

    files: list[dict] = []
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise ValueError("manifest_files entries must be objects.")
        relative_path = _safe_relative_path(
            str(raw.get("relative_path") or ""),
            package_path,
        )
        spec = DriveFileSpec.from_payload(raw)
        if spec.size is None:
            raise ValueError(f"{relative_path} is missing Drive file size.")
        path_key = relative_path.casefold()
        if path_key in seen_paths:
            raise ValueError(f"Duplicate package path: {relative_path}")
        if spec.file_id in seen_ids:
            raise ValueError(f"Duplicate Drive file ID: {spec.file_id}")
        seen_paths.add(path_key)
        seen_ids.add(spec.file_id)
        files.append(
            {
                "relative_path": relative_path,
                "spec": spec,
            }
        )

    for path in seen_paths:
        if any(str(parent) in seen_paths for parent in PurePosixPath(path).parents if str(parent) != "."):
            raise ValueError("Manifest file is also used as a parent directory.")
    files.sort(key=lambda item: item["relative_path"])
    return package_path, files


def manifest_summary(payload: dict) -> dict:
    package_path, files = normalize_manifest(payload)
    model_bytes = 0
    support_bytes = 0
    model_count = 0
    support_count = 0
    model_suffixes = {
        ".gguf", ".safetensors", ".onnx", ".pt", ".pth",
        ".ckpt", ".bin", ".model", ".tflite",
    }
    for item in files:
        spec: DriveFileSpec = item["spec"]
        size = int(spec.size or 0)
        if Path(spec.name).suffix.lower() in model_suffixes:
            model_count += 1
            model_bytes += size
        else:
            support_count += 1
            support_bytes += size
    return {
        "package_path": package_path,
        "file_count": len(files),
        "model_file_count": model_count,
        "support_file_count": support_count,
        "total_bytes": model_bytes + support_bytes,
        "model_bytes": model_bytes,
        "support_bytes": support_bytes,
    }


class PackageRuntime:
    def __init__(
        self,
        drive_cache: DriveCache,
        token_provider: Callable[[], str],
    ) -> None:
        self.drive_cache = drive_cache
        self.token_provider = token_provider
        self.root = PACKAGE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.reset()

    def reset(self) -> None:
        self.package_path: str | None = None
        self.package_dir: str | None = None
        self.phase = "idle"
        self.detail = ""
        self.error: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.current_file: str | None = None
        self.current_index = 0
        self.file_count = 0
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.cached_bytes = 0
        self.materialized_files = 0

    def snapshot(self) -> dict:
        with self.lock:
            running = bool(
                self.thread
                and self.thread.is_alive()
            )
            progress = (
                min(1.0, self.downloaded_bytes / self.total_bytes)
                if self.total_bytes > 0
                else None
            )
            return {
                "running": running,
                "phase": self.phase,
                "detail": self.detail,
                "package_path": self.package_path,
                "package_dir": self.package_dir,
                "current_file": self.current_file,
                "current_index": self.current_index,
                "file_count": self.file_count,
                "downloaded_bytes": self.downloaded_bytes,
                "total_bytes": self.total_bytes,
                "download_progress": progress,
                "cached_bytes": self.cached_bytes,
                "materialized_files": self.materialized_files,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }

    def stop(self, package_path: str | None = None) -> dict:
        with self.lock:
            if package_path is not None and package_path != self.package_path:
                raise ValueError("模型包任务已切换，请刷新状态后重试。")
            if self.thread and self.thread.is_alive() and self.phase not in {"complete", "failed", "cancelled"}:
                self.cancel.set()
                self.phase = "cancelling"
                self.detail = "正在取消模型包物化，等待当前文件操作退出"
        return self.snapshot()

    def start(self, payload: dict) -> dict:
        package_path, files = normalize_manifest(payload)
        token = self.token_provider()

        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("已有模型包正在物化。")
            self.cancel.clear()
            self.reset()
            self.package_path = package_path
            destination = self.root / package_cache_key(package_path)
            self.package_dir = str(destination)
            self.phase = "starting"
            self.detail = "正在检查 Drive 模型包缓存"
            self.started_at = time.time()
            self.file_count = len(files)
            self.total_bytes = sum(int(item["spec"].size or 0) for item in files)

            thread = threading.Thread(
                target=self._run,
                args=(package_path, files, token, destination),
                daemon=True,
            )
            self.thread = thread
            thread.start()
        return self.snapshot()

    def _package_progress(
        self,
        base_completed: int,
        received: int,
        total: int | None,
    ) -> None:
        if self.cancel.is_set():
            raise RuntimeError("任务已取消。")
        with self.lock:
            current = int(received)
            expected = int(total or current)
            current = min(current, expected)
            self.downloaded_bytes = min(
                self.total_bytes,
                base_completed + current,
            )

    def _materialize_one(
        self,
        cached: Path,
        destination: Path,
        size: int,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_dir():
                raise RuntimeError(f"Package path is a directory: {destination.name}")
            try:
                if destination.is_file() and os.path.samefile(cached, destination):
                    return
            except OSError:
                pass
            destination.unlink()

        try:
            os.link(cached, destination)
            return
        except OSError as error:
            if size > _COPY_FALLBACK_LIMIT:
                raise RuntimeError(
                    "无法为大型模型文件创建本地硬链接；为避免重复占用磁盘，"
                    f"已停止而不是复制 {size / (1024**3):.1f} GB 文件。"
                ) from error
        shutil.copy2(cached, destination)

    def _write_manifest(
        self,
        destination: Path,
        package_path: str,
        files: list[dict],
    ) -> None:
        manifest = {
            "schema_version": 1,
            "package_path": package_path,
            "materialized_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(),
            ),
            "files": [
                {
                    "relative_path": item["relative_path"],
                    "drive_file_id": item["spec"].file_id,
                    "name": item["spec"].name,
                    "size": item["spec"].size,
                    "md5_checksum": item["spec"].md5_checksum,
                }
                for item in files
            ],
        }
        manifest_path = destination / ".jvust-package.json"
        temporary = destination / ".jvust-package.json.tmp"
        if manifest_path.is_symlink() or temporary.is_symlink():
            raise ValueError("Package manifest must not be a symbolic link.")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(manifest_path)

    def _run(
        self,
        package_path: str,
        files: list[dict],
        token: str,
        destination: Path,
    ) -> None:
        try:
            if destination.is_symlink() or not destination.resolve().is_relative_to(self.root.resolve()):
                raise ValueError("Package directory escapes package root.")
            destination.mkdir(parents=True, exist_ok=True)
            # Invalidate a previous completion marker before replacing any file.
            (destination / ".jvust-package.json").unlink(missing_ok=True)
            completed = 0
            cached_bytes = 0

            for index, item in enumerate(files, start=1):
                if self.cancel.is_set():
                    raise RuntimeError("任务已取消。")

                spec: DriveFileSpec = item["spec"]
                relative_path = item["relative_path"]
                size = int(spec.size or 0)
                with self.lock:
                    self.phase = "downloading"
                    self.detail = f"准备模型包文件 {index}/{len(files)}"
                    self.current_index = index
                    self.current_file = relative_path
                    self.downloaded_bytes = completed

                cached = self.drive_cache.cached_path(spec)
                if cached:
                    cached_bytes += size
                else:
                    cached = self.drive_cache.download(
                        spec,
                        token,
                        lambda received, total, base=completed: self._package_progress(
                            base, received, total
                        ),
                    )

                target = destination.joinpath(*PurePosixPath(relative_path).parts)
                if self.cancel.is_set():
                    raise RuntimeError("任务已取消。")
                if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("Manifest path escapes package directory.")
                self._materialize_one(cached, target, size)
                completed += size
                with self.lock:
                    self.downloaded_bytes = completed
                    self.cached_bytes = cached_bytes
                    self.materialized_files = index

            with self.lock:
                if self.cancel.is_set():
                    raise RuntimeError("任务已取消。")
                self._write_manifest(destination, package_path, files)
                self.phase = "complete"
                self.detail = "Drive 模型包已完整物化"
                self.current_file = None
                self.downloaded_bytes = self.total_bytes
                self.cached_bytes = cached_bytes
                self.finished_at = time.time()
                self.error = None

        except Exception as error:
            with self.lock:
                if self.cancel.is_set():
                    self.phase = "cancelled"
                    self.detail = "模型包物化已取消"
                else:
                    self.phase = "failed"
                    self.detail = "模型包物化失败"
                self.error = str(error)
                self.finished_at = time.time()
