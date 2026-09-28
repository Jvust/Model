"""Opt-in, read-only desktop model source. No browser-supplied filesystem paths.

Directory authorization happens through a native Windows picker (or an explicit
local config file). Scanning reads names/stat plus bounded model_metadata.json,
not model weights. Only handles from the current completed scan may be opened.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

try:
    from .drive_cache import DriveCache, DriveFileSpec
except ImportError:
    from drive_cache import DriveCache, DriveFileSpec

PREFIX = "desktop_"
FOLDER = "application/vnd.google-apps.folder"
WEIGHTS = {".gguf", ".safetensors", ".onnx", ".pt", ".pth", ".ckpt", ".bin", ".model", ".tflite"}
SUPPORT = {".json", ".txt", ".yaml", ".yml", ".tiktoken", ".jinja"}
MAX_METADATA = 16 * 1024**2
CHUNK = 8 * 1024**2
RECALL_MASK = 0x1000 | 0x40000 | 0x400000  # OFFLINE / RECALL_ON_OPEN / RECALL_ON_DATA_ACCESS
LINK_TAGS = {0xA0000003, 0xA000000C}  # mount-point/junction, symbolic link; NOT cloud tags


def local_settings_path() -> Path:
    override = os.environ.get("MODEL_DESKTOP_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    home = Path(os.environ["LOCALAPPDATA"]) / "JvustModel" if os.name == "nt" else Path.home() / ".config" / "JvustModel"
    return home / "desktop-source.json"


def fingerprint(info) -> tuple[int, int, int, int]:
    return (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino)


def is_link(path: Path, info=None) -> bool:
    info = info or path.lstat()
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in LINK_TAGS


def safe_parts(relative: str) -> tuple[str, ...]:
    parts = PurePosixPath(relative).parts
    if not parts or "\\" in relative or ":" in relative or "\x00" in relative or PurePosixPath(relative).is_absolute():
        raise ValueError("Invalid desktop relative path")
    if any(part in {".", ".."} or part.endswith((".", " ")) for part in relative.split("/")):
        raise ValueError("Invalid desktop relative path")
    return parts


@dataclass(frozen=True)
class LocalFile:
    root: Path
    relative: str
    version: tuple[int, int, int, int]

    def checked_path(self) -> Path:
        target = self.root
        for part in safe_parts(self.relative):
            target = target / part
            if is_link(target):
                raise PermissionError("Desktop symlinks/junctions are not followed")
        if not target.resolve().is_relative_to(self.root) or not target.is_file():
            raise PermissionError("Desktop file is outside the selected directory")
        if fingerprint(target.stat()) != self.version:
            raise ValueError("Desktop file changed since the scan; scan the directory again")
        return target


class DesktopSource:
    def __init__(self, cache_root: Path, config_path: Path | None = None):
        self.cache_root = cache_root.resolve()
        self.config_path = config_path or local_settings_path()
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.instance_id = uuid.uuid4().hex
        self.files: dict[str, LocalFile] = {}
        self.snapshot_data: dict | None = None
        self.state = {"phase": "idle", "folders": 0, "files": 0, "error": None}

    def configured_root(self) -> Path:
        if not self.config_path.is_file():
            raise ValueError("尚未选择桌面版模型目录；请在 Runtime 电脑上点击“选择目录”")
        raw = self.config_path.read_text(encoding="utf-8-sig")
        if len(raw) > 16384:
            raise ValueError("Desktop source config is too large")
        config = json.loads(raw)
        path = Path(config["root"]).expanduser()
        if not path.is_absolute():
            raise ValueError("Desktop source must be an absolute local path")
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("桌面版目录不可用；确认 Drive 已登录、挂载且路径未改变")
        if self.cache_root.is_relative_to(root) or root.is_relative_to(self.cache_root):
            raise ValueError("模型源与 Runtime 缓存目录不能互相包含")
        if self.config_path.resolve().is_relative_to(root):
            raise ValueError("Desktop settings must be stored outside the model source")
        return root

    def configure(self, directory: str) -> None:
        """Local-only API, invoked AFTER native user selection; never exposed as HTTP input."""
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise ValueError("Select an absolute model directory")
        root = path.resolve(strict=True)
        if not root.is_dir() or root == Path(root.anchor):
            raise ValueError("请选择具体模型目录，不要选择整个磁盘")
        if self.cache_root.is_relative_to(root) or root.is_relative_to(self.cache_root):
            raise ValueError("模型源不能与 Runtime 缓存目录重叠")
        if self.config_path.resolve().is_relative_to(root):
            raise ValueError("配置文件不能写入模型源目录")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config_path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "root": str(root), "read_only": True}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.config_path)
        with self.lock:
            self.files = {}
            self.snapshot_data = None
            self.state.update(phase="configured", error=None, folders=0, files=0)

    def status(self) -> dict:
        with self.lock:
            result = dict(self.state)
            result.update(instance_id=self.instance_id, snapshot_ready=self.snapshot_data is not None,
                          busy=bool(self.thread and self.thread.is_alive()), read_only=True)
        try:
            result.update(configured=True, available=True, label=self.configured_root().name)
        except (OSError, ValueError, KeyError, TypeError) as error:
            result.update(configured=self.config_path.is_file(), available=False, label="", configuration_error="桌面版目录未配置或不可用；请检查挂载并重新选择目录")
        return result

    def pick(self) -> dict:
        if os.name != "nt":
            raise ValueError("原生目录选择器只适用于 Windows；开发环境请使用本地 desktop-source.json")
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("桌面版目录操作正在进行")
            self.state.update(phase="selecting", error=None)
            self.thread = threading.Thread(target=self._pick, daemon=True)
            self.thread.start()
        return self.status()

    def _pick(self):
        # All commands are constant. No path, shell expression or executable comes from HTTP.
        script = '''[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = "Model: select AI-Model-Vault in Google Drive for desktop (read-only)"
$d.ShowNewFolderButton = $false
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Write($d.SelectedPath) }
$d.Dispose()
'''
        try:
            exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            result = subprocess.run([str(exe), "-NoProfile", "-STA", "-Command", script], capture_output=True,
                                    text=True, encoding="utf-8-sig", timeout=180, check=True)
            chosen = result.stdout.strip()
            if chosen:
                self.configure(chosen)
            else:
                with self.lock:
                    self.state["phase"] = "cancelled"
        except Exception as error:
            with self.lock:
                self.state.update(phase="failed", error="无法完成本机目录选择：" + type(error).__name__)

    def start_scan(self) -> dict:
        root = self.configured_root()
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("桌面版目录操作正在进行")
            self.cancel.clear()
            self.files = {}
            self.snapshot_data = None
            self.state = {"phase": "scanning", "folders": 0, "files": 0, "error": None}
            self.thread = threading.Thread(target=self._scan_job, args=(root,), daemon=True)
            self.thread.start()
        return self.status()

    def stop(self):
        self.cancel.set()
        return self.status()

    def _scan_job(self, root):
        try:
            snapshot, files = self.scan(root)
            if self.cancel.is_set():
                raise InterruptedError("Desktop scan cancelled")
            with self.lock:
                self.files, self.snapshot_data = files, snapshot
                self.state.update(phase="complete", warnings=snapshot["warnings"])
        except Exception as error:
            with self.lock:
                self.files, self.snapshot_data = {}, None
                self.state.update(phase="cancelled" if self.cancel.is_set() else "failed", error=str(error))

    def scan(self, root: Path, max_folders=2000, max_files=60000, max_depth=24) -> tuple[dict, dict]:
        root = root.resolve(strict=True)
        records, warnings = {}, []
        counts = {"folders": 0, "files": 0, "entries": 0}
        source_key = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()

        def walk(path, relative="", depth=0):
            if self.cancel.is_set():
                raise InterruptedError("Desktop scan cancelled")
            if depth > max_depth:
                raise ValueError("目录层级超过扫描上限，请选择更具体的模型目录")
            counts["folders"] += 1
            if counts["folders"] > max_folders:
                raise ValueError("文件夹数量超过扫描上限；未保存不完整索引")
            node = {"file": {"id": PREFIX + hashlib.sha256((source_key+relative).encode()).hexdigest(), "name": path.name, "mimeType": FOLDER},
                    "relativePath": relative, "scanned": True, "children": []}
            # Enumerating a streamed folder can fetch metadata; never open weight files here.
            with os.scandir(path) as entries:
                for entry in entries:
                    if self.cancel.is_set():
                        raise InterruptedError("Desktop scan cancelled")
                    counts["entries"] += 1
                    if counts["entries"] > max_files * 4:
                        raise ValueError("扫描条目过多；未保存不完整索引")
                    if entry.name.startswith(".") or entry.name == "__pycache__":
                        continue
                    rel = relative + "/" + entry.name if relative else entry.name
                    # DirEntry.stat() omits Windows volume/file IDs. Use the same
                    # metadata source as later path/fd checks; do not weaken identity validation.
                    info = os.stat(entry.path, follow_symlinks=False)
                    if is_link(Path(entry.path), info):
                        if len(warnings) < 30:
                            warnings.append("跳过链接或 junction：" + rel)
                        continue
                    safe_parts(rel)
                    if stat.S_ISDIR(info.st_mode):
                        node["children"].append(walk(Path(entry.path), rel, depth+1))
                    elif stat.S_ISREG(info.st_mode):
                        ext = Path(entry.name).suffix.lower()
                        if ext not in WEIGHTS | SUPPORT:
                            continue
                        # Support files can legitimately be large (for example tokenizer.json).
                        # Index them by stat only; scanning must not read their contents.
                        # MAX_METADATA applies only to the root model_metadata.json content read below.
                        if info.st_size <= 0:
                            if len(warnings) < 30:
                                warnings.append("空文件未加入可用清单：" + rel)
                            continue
                        counts["files"] += 1
                        if counts["files"] > max_files:
                            raise ValueError("文件数量超过扫描上限；未保存不完整索引")
                        version = fingerprint(info)
                        key = PREFIX + hashlib.sha256(json.dumps([source_key, rel, version]).encode()).hexdigest()
                        records[key] = LocalFile(root, rel, version)
                        attrs = getattr(info, "st_file_attributes", 0)
                        file = {"id": key, "name": entry.name, "mimeType": "application/octet-stream", "size": str(info.st_size),
                                "source": "desktop", "mayRequireDownload": bool(attrs & RECALL_MASK)}
                        node["children"].append({"file": file, "relativePath": rel, "scanned": True, "children": []})
            with self.lock:
                self.state.update(folders=counts["folders"], files=counts["files"])
            return node

        tree = walk(root)
        registry = None
        item = next((item for item in records.values() if item.relative == "model_metadata.json"), None)
        if item:
            path = item.checked_path()
            with path.open("rb") as stream:
                raw = stream.read(MAX_METADATA + 1)
            if len(raw) > MAX_METADATA:
                raise ValueError("模型登记表超过 16 MiB")
            registry = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(registry, dict) or not isinstance(registry.get("models"), list):
                raise ValueError("model_metadata.json 格式无效")
            item.checked_path()
        else:
            warnings.append("未找到 model_metadata.json；仅按目录和扩展名识别，专用模型需要登记表")
        return {"rootFolder": tree["file"], "tree": tree, "registry": registry, "complete": True,
                "source": "desktop", "instance_id": self.instance_id, "warnings": warnings, **counts}, records

    def resolve(self, spec: DriveFileSpec) -> LocalFile:
        with self.lock:
            item = self.files.get(spec.file_id)
        if not item:
            raise ValueError("桌面版文件句柄失效；请在当前 Runtime 重新扫描目录")
        if spec.name != PurePosixPath(item.relative).name or spec.size != item.version[0]:
            raise ValueError("Desktop metadata does not match the scanned file")
        if self.configured_root() != item.root:
            raise ValueError("Desktop root changed; scan again")
        item.checked_path()
        return item


def payload_specs(payload: dict) -> list[DriveFileSpec]:
    candidates = []
    for field in ("files", "manifest_files"):
        if isinstance(payload.get(field), list):
            candidates.extend(payload[field])
    if payload.get("drive_file_id"):
        candidates.append(payload)
    specs = []
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("Source files must be objects")
        data = dict(item)
        data["drive_file_id"] = item.get("drive_file_id") or item.get("id")
        data["file_name"] = item.get("file_name") or item.get("name")
        specs.append(DriveFileSpec.from_payload(data))
    return specs


class DesktopAwareCache(DriveCache):
    def __init__(self, root: Path, source: DesktopSource):
        super().__init__(root)
        self.source = source

    def access_token(self, payload: dict, provider: Callable[[], str]) -> str:
        specs = payload_specs(payload)
        local = [spec.file_id.startswith(PREFIX) for spec in specs]
        if any(local):
            if not all(local):
                raise ValueError("Desktop and Drive API files cannot be mixed in one task")
            for spec in specs:
                self.source.resolve(spec)
            return ""  # No OAuth credential exists or is needed for read-only local files.
        return provider()

    def cached_path(self, spec):
        if spec.file_id.startswith(PREFIX):
            self.source.resolve(spec)
        return super().cached_path(spec)

    def download(self, spec, access_token, progress=None):
        if not spec.file_id.startswith(PREFIX):
            return super().download(spec, access_token, progress)
        with self._lock:
            item = self.source.resolve(spec)
            cached = self.cached_path(spec)
            if cached:
                if progress:
                    progress(spec.size, spec.size)
                return cached
            final, partial, metadata = self._paths(spec)
            check = self.download_preflight(spec)
            if not check["ok"]:
                raise RuntimeError("本机缓存空间不足，无法读取桌面版模型；Drive 流式缓存也可能另占空间")
            source_path = item.checked_path()
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(source_path, flags)
            with os.fdopen(descriptor, "rb") as source:
                if fingerprint(os.fstat(source.fileno())) != item.version:
                    raise ValueError("Desktop file changed before reading")
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > spec.size:
                    partial.unlink()
                    offset = 0
                digest = hashlib.sha256()
                # Revalidate ALL partial bytes against the current source before append.
                # Do not trust a .part just because its length fits.
                matched = True
                if offset:
                    with partial.open("rb") as previous:
                        remaining = offset
                        while remaining:
                            chunk = previous.read(min(CHUNK, remaining))
                            original = source.read(len(chunk))
                            if not chunk or original != chunk:
                                matched = False
                                break
                            digest.update(chunk)
                            remaining -= len(chunk)
                            if progress:
                                progress(offset-remaining, spec.size)
                    if not matched:
                        offset = 0
                        digest = hashlib.sha256()
                        source.seek(0)
                copied = offset
                if progress:
                    progress(copied, spec.size)
                with partial.open("ab" if offset else "wb") as destination:
                    while copied < spec.size:
                        chunk = source.read(min(CHUNK, spec.size-copied))
                        if not chunk:
                            raise OSError("桌面版文件读取未完成；确认 Drive 已连接或文件可离线使用")
                        destination.write(chunk)
                        digest.update(chunk)
                        copied += len(chunk)
                        if progress:
                            progress(copied, spec.size)
                    destination.flush()
                    os.fsync(destination.fileno())
                if source.read(1) or fingerprint(os.fstat(source.fileno())) != item.version:
                    raise ValueError("Desktop file changed during reading; scan again")
            item.checked_path()
            if partial.stat().st_size != spec.size:
                raise ValueError("Desktop copy size mismatch")
            partial.replace(final)
            temporary = metadata.with_suffix(".json.tmp")
            temporary.write_text(json.dumps({"file_id": spec.file_id, "name": spec.name, "size": spec.size,
                "source": "desktop", "status": "complete", "sha256": digest.hexdigest(), "verified_mtime_ns": final.stat().st_mtime_ns, "read_only_source": True}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(metadata)
            return final
