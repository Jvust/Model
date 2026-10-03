from __future__ import annotations

import atexit
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

try:
    from .backends import backend_status, model_plan
    from .drive_cache import DriveCache, DriveFileSpec
    from .hardware import disk_status, gguf_preflight, nvidia_status, system_memory_status
    from .model_capabilities import all_capabilities
    from .package_runtime import PackageRuntime, manifest_summary
    from .edit_runtime import EditRuntime
    from .task_runtime import TaskRuntime, adapter_for as task_adapter_for
    from .image_runtime import (
        ImageRuntime,
        adapter_for as image_adapter_for,
        artifact_specs as image_artifact_specs,
        managed_comfy_hardware,
    )
    from .video_runtime import (
        VideoRuntime,
        adapter_for as video_adapter_for,
        adapter_hardware as video_adapter_hardware,
        nvidia_available,
    )
except ImportError:
    from backends import backend_status, model_plan
    from drive_cache import DriveCache, DriveFileSpec
    from hardware import disk_status, gguf_preflight, nvidia_status, system_memory_status
    from model_capabilities import all_capabilities
    from package_runtime import PackageRuntime, manifest_summary
    from edit_runtime import EditRuntime
    from task_runtime import TaskRuntime, adapter_for as task_adapter_for
    from image_runtime import (
        ImageRuntime,
        adapter_for as image_adapter_for,
        artifact_specs as image_artifact_specs,
        managed_comfy_hardware,
    )
    from video_runtime import (
        VideoRuntime,
        adapter_for as video_adapter_for,
        adapter_hardware as video_adapter_hardware,
        nvidia_available,
    )

HOST = "127.0.0.1"
BRIDGE_PORT = int(os.environ.get("MODEL_BRIDGE_PORT", "8765"))
MODEL_SERVER_PORT = int(os.environ.get("MODEL_SERVER_PORT", "8080"))
LLAMA_SERVER_PATH = os.environ.get("LLAMA_SERVER_PATH", "llama-server")
MODEL_GPU_LAYERS = int(os.environ.get("MODEL_GPU_LAYERS", "0"))
MODEL_THREADS = int(
    os.environ.get(
        "MODEL_THREADS",
        str(max(1, (os.cpu_count() or 4) - 2)),
    )
)

ALLOWED_LOAD_MODES = {"auto", "none", "mmap", "mlock", "mmap+mlock", "dio"}


def normalize_load_mode(value: str) -> str:
    mode = str(value or "none").strip().lower()
    if mode not in ALLOWED_LOAD_MODES:
        allowed = ", ".join(sorted(ALLOWED_LOAD_MODES))
        raise ValueError(f"Invalid MODEL_LOAD_MODE={value!r}. Allowed: {allowed}.")
    return mode


def positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0.")
    return value


MODEL_LOAD_MODE = normalize_load_mode(os.environ.get("MODEL_LOAD_MODE", "none"))
MODEL_READY_WARN_SECONDS = positive_float_env("MODEL_READY_WARN_SECONDS", 300.0)
MODEL_HEALTH_INTERVAL = positive_float_env("MODEL_HEALTH_INTERVAL", 0.5)
MODEL_CHAT_TIMEOUT = positive_float_env("MODEL_CHAT_TIMEOUT", 600.0)
REMOTE_TOKEN = os.environ.get("MODEL_REMOTE_TOKEN", "").strip()

DEFAULT_ORIGINS = ",".join(
    [
        "https://jvust.github.io",
        "https://jvust2.github.io",
        "https://jvust1.github.io",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]
)
ALLOWED_ORIGINS = {
    item.strip()
    for item in os.environ.get("MODEL_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",")
    if item.strip()
}


def remote_token_valid(
    configured: str | None,
    authorization: str | None = None,
    fallback: str | None = None,
) -> bool:
    expected = str(configured or "").strip()
    if not expected:
        return True

    auth = str(authorization or "").strip()
    candidate = ""
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
    if not candidate:
        candidate = str(fallback or "").strip()
    if not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


def resolve_llama_server() -> str | None:
    configured = str(LLAMA_SERVER_PATH or "").strip()
    if not configured:
        return None

    expanded = os.path.expandvars(os.path.expanduser(configured))
    if os.path.dirname(expanded):
        candidate = Path(expanded)
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())
        return None
    return shutil.which(expanded)


def inspect_gguf(model_path: Path) -> dict:
    """Read only the fixed GGUF header; never scan tensor payloads."""
    with model_path.open("rb") as handle:
        header = handle.read(24)

    if len(header) < 24:
        raise ValueError("GGUF file is too small to contain a complete header.")

    magic, version, tensor_count, metadata_kv_count = struct.unpack("<4sIQQ", header)
    if magic != b"GGUF":
        raise ValueError("File extension is .gguf but the GGUF magic header is missing.")

    return {
        "format": "gguf",
        "version": version,
        "tensor_count": tensor_count,
        "metadata_kv_count": metadata_kv_count,
        "file_size": model_path.stat().st_size,
        "header_bytes_read": len(header),
    }


def llama_health_status() -> dict:
    url = f"http://127.0.0.1:{MODEL_SERVER_PORT}/health"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=0.75) as response:
            status = int(response.status)
            return {"reachable": True, "ready": status == 200, "status": status}
    except HTTPError as error:
        return {"reachable": True, "ready": False, "status": int(error.code)}
    except (URLError, TimeoutError, socket.timeout, OSError):
        return {"reachable": False, "ready": False, "status": None}


class DriveSession:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.access_token: str | None = None

    def set(self, access_token: str) -> None:
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("Google Drive access token is required.")
        if len(token) > 8192:
            raise ValueError("Google Drive access token is unexpectedly large.")
        with self.lock:
            self.access_token = token

    def get(self) -> str:
        with self.lock:
            if not self.access_token:
                raise PermissionError(
                    "Runtime has no Google Drive session. Reconnect Drive in the website."
                )
            return self.access_token

    def clear(self) -> None:
        with self.lock:
            self.access_token = None


DRIVE_SESSION = DriveSession()
DRIVE_CACHE = DriveCache()


def runtime_hardware_snapshot() -> dict:
    managed = managed_comfy_hardware()
    gpu = nvidia_status()
    return {
        "nvidia": bool(gpu.get("detected")),
        "gpu": gpu,
        "memory": system_memory_status(),
        "cache_disk": disk_status(DRIVE_CACHE.root),
        "managed_comfy_supported": bool(managed.get("supported")),
        "managed_comfy_detail": str(managed.get("detail") or ""),
    }


def build_chat_payload(payload: dict, model_name: str | None) -> dict:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array.")
    if len(messages) > 64:
        raise ValueError("Too many chat messages; maximum is 64.")

    clean_messages = []
    total_chars = 0
    for item in messages:
        if not isinstance(item, dict):
            raise ValueError("Each chat message must be an object.")
        role = str(item.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            raise ValueError("Chat message role must be system, user, or assistant.")
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError("Chat message content must be text.")
        total_chars += len(content)
        if total_chars > 131072:
            raise ValueError("Chat history is too large for the local bridge.")
        clean_messages.append({"role": role, "content": content})

    try:
        temperature = float(payload.get("temperature", 0.7))
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature must be a number.") from exc
    if not 0 <= temperature <= 2:
        raise ValueError("temperature must be between 0 and 2.")

    try:
        max_tokens = int(payload.get("max_tokens", 512))
    except (TypeError, ValueError) as exc:
        raise ValueError("max_tokens must be an integer.") from exc
    if not 1 <= max_tokens <= 4096:
        raise ValueError("max_tokens must be between 1 and 4096.")

    return {
        "model": str(model_name or "local-model"),
        "messages": clean_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }


def llama_chat_completion(payload: dict) -> tuple[int, dict]:
    request = Request(
        f"http://127.0.0.1:{MODEL_SERVER_PORT}/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer no-key",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=MODEL_CHAT_TIMEOUT) as response:
            raw = response.read()
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("llama-server returned a non-object JSON response.")
            return int(response.status), data
    except HTTPError as error:
        raw = error.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = {"error": raw.decode("utf-8", errors="replace") or str(error)}
        if not isinstance(data, dict):
            data = {"error": str(data)}
        return int(error.code), data
    except (URLError, TimeoutError, socket.timeout, OSError) as error:
        raise RuntimeError("Could not reach llama-server: " + str(error)) from error


class RuntimeState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.model: str | None = None
        self.model_relative_path: str | None = None
        self.source_drive_file_id: str | None = None
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.phase = "idle"
        self.last_error: str | None = None
        self.exit_code: int | None = None
        self.downloaded_bytes = 0
        self.download_total_bytes: int | None = None
        self.cache_file: str | None = None
        self.job_id = 0
        self.logs: deque[str] = deque(maxlen=300)

    def append_log(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        with self.lock:
            self.logs.append(line)

    def snapshot(self) -> dict:
        with self.lock:
            process = self.process
            running = bool(process and process.poll() is None)
            started_at = self.started_at if running else None
            uptime = max(0.0, time.time() - started_at) if started_at else None
            total = self.download_total_bytes
            downloaded = self.downloaded_bytes
            progress = (
                min(1.0, downloaded / total)
                if total and total > 0
                else None
            )
            return {
                "running": running,
                "ready": running and self.phase == "ready",
                "phase": self.phase,
                "pid": process.pid if running and process else None,
                "model": self.model,
                "model_relative_path": self.model_relative_path,
                "source_drive_file_id": self.source_drive_file_id,
                "started_at": started_at,
                "ready_at": self.ready_at if running else None,
                "uptime_seconds": uptime,
                "server_url": f"http://127.0.0.1:{MODEL_SERVER_PORT}" if running else None,
                "cpu_threads": MODEL_THREADS,
                "gpu_layers": MODEL_GPU_LAYERS,
                "load_mode": MODEL_LOAD_MODE,
                "downloaded_bytes": downloaded,
                "download_total_bytes": total,
                "download_progress": progress,
                "cache_file": self.cache_file,
                "last_error": self.last_error,
                "exit_code": self.exit_code,
                "logs": list(self.logs),
            }

    def stop(self) -> None:
        with self.lock:
            self.job_id += 1
            process = self.process
            self.process = None
            self.model = None
            self.model_relative_path = None
            self.source_drive_file_id = None
            self.started_at = None
            self.ready_at = None
            self.phase = "idle"
            self.last_error = None
            self.exit_code = None
            self.downloaded_bytes = 0
            self.download_total_bytes = None
            self.cache_file = None

        if not process or process.poll() is not None:
            return

        self.append_log("Stopping llama-server...")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def start_drive(
        self,
        spec: DriveFileSpec,
        model_name: str,
        relative_path: str,
        access_token: str,
    ) -> dict:
        self.stop()
        with self.lock:
            job_id = self.job_id
            self.model = model_name
            self.model_relative_path = relative_path
            self.source_drive_file_id = spec.file_id
            self.phase = "downloading"
            self.downloaded_bytes = 0
            self.download_total_bytes = spec.size
            self.last_error = None
            self.exit_code = None

        cached = DRIVE_CACHE.cached_path(spec)
        if cached:
            self.append_log(f"Using cached Drive model: {model_name}")
            with self.lock:
                self.downloaded_bytes = cached.stat().st_size
                self.download_total_bytes = spec.size or cached.stat().st_size
                self.cache_file = cached.name
            self._launch_cached(job_id, cached, model_name, relative_path)
            return self.snapshot()

        self.append_log(f"Downloading from Google Drive: {model_name}")
        threading.Thread(
            target=self._download_and_launch,
            args=(job_id, spec, model_name, relative_path, access_token),
            daemon=True,
        ).start()
        return self.snapshot()

    def _download_and_launch(
        self,
        job_id: int,
        spec: DriveFileSpec,
        model_name: str,
        relative_path: str,
        access_token: str,
    ) -> None:
        def progress(received: int, total: int | None) -> None:
            with self.lock:
                if self.job_id != job_id:
                    return
                self.downloaded_bytes = received
                self.download_total_bytes = total

        try:
            model_path = DRIVE_CACHE.download(spec, access_token, progress)
            with self.lock:
                if self.job_id != job_id:
                    return
                self.cache_file = model_path.name
            self.append_log(f"Drive download complete: {model_name}")
            self._launch_cached(job_id, model_path, model_name, relative_path)
        except Exception as error:
            with self.lock:
                if self.job_id != job_id:
                    return
                self.phase = "failed"
                self.last_error = str(error)
            self.append_log("Drive cache failed: " + str(error))

    def _launch_cached(
        self,
        job_id: int,
        model_path: Path,
        model_name: str,
        relative_path: str,
    ) -> None:
        if model_path.suffix.lower() != ".gguf":
            raise ValueError("llama.cpp direct launch requires a GGUF file.")

        gguf = inspect_gguf(model_path)
        executable = resolve_llama_server()
        if not executable:
            raise FileNotFoundError(
                "llama-server was not found. Set LLAMA_SERVER_PATH to llama-server.exe."
            )

        command = [
            executable,
            "-m",
            str(model_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(MODEL_SERVER_PORT),
            "-t",
            str(MODEL_THREADS),
            "-ngl",
            str(MODEL_GPU_LAYERS),
            "--load-mode",
            MODEL_LOAD_MODE,
        ]

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        self.append_log(
            "Launching llama-server from Drive cache: "
            f"model={model_name} threads={MODEL_THREADS} "
            f"gpu_layers={MODEL_GPU_LAYERS} load_mode={MODEL_LOAD_MODE}"
        )

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )

        with self.lock:
            if self.job_id != job_id:
                process.terminate()
                return
            self.process = process
            self.started_at = time.time()
            self.ready_at = None
            self.phase = "loading"
            self.last_error = None
            self.exit_code = None

        threading.Thread(target=self._read_output, args=(process,), daemon=True).start()
        threading.Thread(target=self._monitor_ready, args=(process,), daemon=True).start()

        time.sleep(0.15)
        exit_code = process.poll()
        if exit_code is not None:
            message = f"llama-server exited immediately with code {exit_code}."
            self._mark_failed(process, message, exit_code)
            raise RuntimeError(message + " Check runtime logs and LLAMA_SERVER_PATH.")

        self.append_log(
            f"Cached GGUF verified: v{gguf['version']} tensors={gguf['tensor_count']}"
        )

    def _mark_failed(
        self, process: subprocess.Popen[str], message: str, exit_code: int | None = None
    ) -> None:
        with self.lock:
            if self.process is not process:
                return
            self.phase = "failed"
            self.last_error = message
            self.exit_code = exit_code
        self.append_log(message)

    def _monitor_ready(self, process: subprocess.Popen[str]) -> None:
        started = time.monotonic()
        warned = False

        while process.poll() is None:
            health = llama_health_status()
            if health["ready"]:
                with self.lock:
                    if self.process is not process:
                        return
                    self.phase = "ready"
                    self.ready_at = time.time()
                    self.last_error = None
                self.append_log("llama-server is ready (/health = 200).")
                return

            elapsed = time.monotonic() - started
            if not warned and elapsed >= MODEL_READY_WARN_SECONDS:
                warned = True
                message = (
                    f"llama-server is still loading after {MODEL_READY_WARN_SECONDS:.0f}s; "
                    "the process remains running and readiness checks continue."
                )
                with self.lock:
                    if self.process is not process:
                        return
                    self.last_error = message
                self.append_log(message)

            time.sleep(MODEL_HEALTH_INTERVAL)

        exit_code = process.poll()
        self._mark_failed(
            process,
            f"llama-server exited before becoming ready with code {exit_code}.",
            exit_code,
        )

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout:
            for line in process.stdout:
                self.append_log(line)

        exit_code = process.poll()
        if exit_code is None:
            try:
                exit_code = process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                return

        with self.lock:
            if self.process is not process:
                return
            was_ready = self.phase == "ready"

        if was_ready:
            self._mark_failed(
                process,
                f"llama-server exited unexpectedly with code {exit_code}.",
                exit_code,
            )


STATE = RuntimeState()
VIDEO = VideoRuntime()
IMAGE = ImageRuntime(VIDEO, DRIVE_CACHE, DRIVE_SESSION.get)
TASK = TaskRuntime(DRIVE_CACHE, DRIVE_SESSION.get, resolve_llama_server)
PACKAGE = PackageRuntime(DRIVE_CACHE, DRIVE_SESSION.get)
EDIT = EditRuntime(PACKAGE)
GENERATION_LOCK = threading.RLock()
atexit.register(STATE.stop)
atexit.register(PACKAGE.stop)
atexit.register(EDIT.stop)
atexit.register(TASK.stop)
atexit.register(IMAGE.shutdown)
atexit.register(VIDEO.shutdown)



class Handler(BaseHTTPRequestHandler):
    server_version = "DriveModelBridge/0.17"

    def log_message(self, format: str, *args) -> None:
        return

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in ALLOWED_ORIGINS

    def _authorized(self) -> bool:
        return remote_token_valid(
            REMOTE_TOKEN,
            self.headers.get("Authorization"),
            self.headers.get("X-Model-Runtime-Token"),
        )

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Model-Runtime-Token",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, max_bytes: int = 262144) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > max_bytes:
            raise ValueError("Request body too large.")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object.")
        return value

    def _serve_video_file(self, path: Path) -> None:
        size = path.stat().st_size
        start = 0
        end = size - 1
        status = 200

        range_header = self.headers.get("Range") or ""
        # Single ranges include suffix requests used by media players. Unsupported
        # multi-ranges are ignored (200), never silently treated as the first range.
        # https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Range
        if range_header.startswith("bytes=") and "," not in range_header:
            value = range_header[6:].strip()
            left, separator, right = value.partition("-")
            valid = bool(separator and (left or right) and
                         (not left or left.isascii() and left.isdecimal()) and
                         (not right or right.isascii() and right.isdecimal()) and
                         len(left) < 20 and len(right) < 20)
            if valid:
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    suffix_length = int(right)
                    start = max(0, size - suffix_length)
                    valid = suffix_length > 0
            if not valid or start < 0 or end < start or start >= size:
                self.send_response(416)
                self._cors_headers()
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206

        content_length = end - start + 1
        suffix = path.suffix.lower()
        content_type = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mkv": "video/x-matroska",
            ".gif": "image/gif",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")

        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        remaining = content_length
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"error": "Origin not allowed."})
            return
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "Drive Model Local Runtime",
                    "version": 17,
                    "remote_auth_required": bool(REMOTE_TOKEN),
                },
            )
            return

        if not self._origin_allowed():
            self._json(403, {"error": "Origin not allowed."})
            return

        media_paths = {"/v1/image/file"}
        if path.startswith("/v1/") and path not in media_paths and not self._authorized():
            self._json(401, {"error": "Runtime authorization required."})
            return

        if path == "/v1/backends":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, backend_status(LLAMA_SERVER_PATH))
            return

        if path == "/v1/hardware":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, {"ok": True, "hardware": runtime_hardware_snapshot()})
            return

        if path == "/v1/runtime":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            payload = STATE.snapshot()
            payload.update(
                {
                    "drive_api_session": bool(DRIVE_SESSION.access_token),
                    "cache_root_label": DRIVE_CACHE.root.name,
                    "llama_server_found": resolve_llama_server() is not None,
                    "bridge_port": BRIDGE_PORT,
                    "model_server_port": MODEL_SERVER_PORT,
                    "ready_warn_seconds": MODEL_READY_WARN_SECONDS,
                    "video": VIDEO.snapshot(),
                    "image": IMAGE.snapshot(),
                    "task": TASK.snapshot(),
                    "package": PACKAGE.snapshot(),
                    "edit": EDIT.snapshot(),
                    "hardware": runtime_hardware_snapshot(),
                    "runtime_version": 17,
                    "remote_auth_required": bool(REMOTE_TOKEN),
                }
            )
            self._json(200, payload)
            return

        if path.startswith("/v1/edit/"):
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            try:
                if path == "/v1/edit/status":
                    self._json(200, EDIT.snapshot())
                elif path == "/v1/edit/environment":
                    self._json(200, EDIT.environment())
                elif path == "/v1/edit/file":
                    query = parse_qs(urlparse(self.path).query)
                    self._serve_video_file(EDIT.output_file(str((query.get("job_id") or [""])[0])))
                else:
                    self._json(404, {"error": "Not found."})
            except FileNotFoundError as error:
                self._json(404, {"error": str(error)})
            except ValueError as error:
                self._json(400, {"error": str(error)})
            except Exception as error:
                self._json(500, {"error": str(error)})
            return

        if path == "/v1/packages/status":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, PACKAGE.snapshot())
            return

        if path == "/v1/tasks/status":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, TASK.snapshot())
            return

        if path == "/v1/models/capabilities":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, {"ok": True, "models": all_capabilities()})
            return

        if path == "/v1/models/cache":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, {"ok": True, "entries": DRIVE_CACHE.list_entries()})
            return

        if path == "/v1/image/status":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, IMAGE.snapshot())
            return

        if path == "/v1/image/file":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            query = parse_qs(urlparse(self.path).query)
            job_id = str((query.get("job_id") or [""])[0])
            try:
                image_path = IMAGE.output_file(job_id)
                self._serve_video_file(image_path)
            except FileNotFoundError as error:
                self._json(404, {"error": str(error)})
            except ValueError as error:
                self._json(400, {"error": str(error)})
            return

        if path == "/v1/video/status":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            self._json(200, VIDEO.snapshot())
            return

        if path == "/v1/video/file":
            if not self._origin_allowed():
                self._json(403, {"error": "Origin not allowed."})
                return
            query = parse_qs(urlparse(self.path).query)
            job_id = str((query.get("job_id") or [""])[0])
            try:
                video_path = VIDEO.output_file(job_id)
                self._serve_video_file(video_path)
            except FileNotFoundError as error:
                self._json(404, {"error": str(error)})
            except ValueError as error:
                self._json(400, {"error": str(error)})
            return

        self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"error": "Origin not allowed."})
            return
        if not self._authorized():
            self._json(401, {"error": "Runtime authorization required."})
            return

        path = urlparse(self.path).path

        try:
            payload = self._read_json(max_bytes=12 * 1024 * 1024 if path == "/v1/edit/generate" else 262144)

            if path == "/v1/edit/generate":
                with GENERATION_LOCK:
                    if IMAGE.snapshot().get("running") or VIDEO.snapshot().get("running") or STATE.snapshot().get("running"):
                        raise ValueError("请先停止其他图像、视频或聊天模型，再运行 2511 编辑。")
                    result = EDIT.start(payload)
                self._json(202, {"ok": True, "edit": result})
                return

            if path == "/v1/edit/stop":
                result = EDIT.stop(job_id=payload.get("job_id")) if payload.get("job_id") else EDIT.stop()
                self._json(200, {"ok": True, "edit": result})
                return

            if path == "/v1/packages/materialize":
                with GENERATION_LOCK:
                    if EDIT.snapshot().get("running"):
                        raise ValueError("请先停止 2511 编辑，再重新准备模型包。")
                    result = PACKAGE.start(payload)
                self._json(202, {"ok": True, "package": result})
                return

            if path == "/v1/packages/stop":
                result = PACKAGE.stop(package_path=payload.get("package_path")) if payload.get("package_path") else PACKAGE.stop()
                self._json(200, {"ok": True, "package": result})
                return

            if path == "/v1/tasks/start":
                result = TASK.start(payload)
                self._json(202, {"ok": True, "task": result})
                return

            if path == "/v1/tasks/stop":
                self._json(200, {"ok": True, "task": TASK.stop()})
                return

            if path == "/v1/tasks/embeddings":
                self._json(200, {"ok": True, "result": TASK.embeddings(payload)})
                return

            if path == "/v1/tasks/rerank":
                self._json(200, {"ok": True, "result": TASK.rerank(payload)})
                return

            if path == "/v1/chat/completions":
                snapshot = STATE.snapshot()
                if not snapshot["ready"]:
                    self._json(409, {"error": "Local model is not ready."})
                    return
                chat_payload = build_chat_payload(payload, snapshot.get("model"))
                status, result = llama_chat_completion(chat_payload)
                self._json(status, result)
                return

            if path == "/v1/models/plan":
                backend = str(payload.get("backend") or "")
                category = str(payload.get("category") or "unknown")
                plan = model_plan(
                    backend,
                    category,
                    model_id=str(payload.get("model_id") or ""),
                    name=str(payload.get("name") or ""),
                    artifact_present=payload.get("artifact_present") if isinstance(payload.get("artifact_present"), bool) else None,
                )
                status = backend_status(LLAMA_SERVER_PATH)["backends"].get(
                    backend,
                    {
                        "detected": False,
                        "automatic_launch": False,
                        "detail": "unknown backend",
                    },
                )
                name = str(payload.get("name") or "")
                model_id = str(payload.get("model_id") or "")
                package_path = str(payload.get("package_path") or "")
                package_manifest = None
                if payload.get("manifest_files") and package_path:
                    try:
                        package_manifest = manifest_summary(payload)
                    except (ValueError, FileNotFoundError) as error:
                        package_manifest = {"error": str(error)}
                video_match = video_adapter_for(name, model_id)
                image_match = image_adapter_for(name, model_id, package_path)
                weight_detail = (
                    "Drive 已扫描到模型包"
                    if plan.get("artifact_present") is True
                    else "Drive 未确认可用模型权重"
                    if plan.get("artifact_present") is False
                    else "权重状态未知"
                )
                if image_match:
                    try:
                        image_specs = image_artifact_specs(payload, image_match[1])
                        plan["artifact_present"] = True
                        weight_detail = (
                            "已确认固定模型文件："
                            + "、".join(spec.name for spec in image_specs.values())
                        )
                    except (FileNotFoundError, ValueError) as error:
                        plan["artifact_present"] = False
                        plan["automatic_launch"] = False
                        plan["availability_label"] = "Drive 文件不完整"
                        plan["availability_reason"] = str(error)
                        weight_detail = str(error)

                managed_adapter = video_match or image_match
                if image_match:
                    hardware = managed_comfy_hardware(image_match[1])
                elif video_match:
                    hardware = video_adapter_hardware(video_match[1])
                else:
                    hardware = managed_comfy_hardware()
                hardware_ready = bool(hardware["supported"])
                artifact_ready = plan.get("artifact_present") is not False
                if managed_adapter:
                    status = {
                        "detected": hardware_ready,
                        "automatic_launch": hardware_ready and artifact_ready,
                        "detail": (
                            "managed ComfyUI; first run prepares it automatically"
                            if hardware_ready
                            else hardware["detail"]
                        ),
                    }
                    if artifact_ready and not hardware_ready:
                        plan["automatic_launch"] = False
                        plan["availability_label"] = "硬件不支持"
                        plan["availability_reason"] = hardware["detail"]
                gguf_status = None
                if backend == "llama.cpp":
                    size_raw = payload.get("size")
                    try:
                        expected_bytes = int(size_raw) if size_raw not in (None, "", 0, "0") else None
                    except (TypeError, ValueError):
                        expected_bytes = None
                    partial_bytes = 0
                    try:
                        if payload.get("drive_file_id"):
                            plan_spec = DriveFileSpec.from_payload(payload)
                            partial_bytes = int(DRIVE_CACHE.describe(plan_spec).get("partial_bytes") or 0)
                    except ValueError:
                        pass
                    gguf_status = gguf_preflight(
                        DRIVE_CACHE.root,
                        expected_bytes=expected_bytes,
                        partial_bytes=partial_bytes,
                        threads=MODEL_THREADS,
                    )
                    if not gguf_status["ok"]:
                        plan["automatic_launch"] = False
                        plan["availability_label"] = "硬件不支持"
                        plan["availability_reason"] = gguf_status["reason"]

                plan.update(
                    {
                        "drive_api_session": bool(DRIVE_SESSION.access_token),
                        "cache_mode": "drive-api",
                        "backend_status": status,
                        "weight_detail": weight_detail,
                        "hardware_supported": (
                            bool(hardware["supported"]) if managed_adapter else None
                        ),
                        "hardware_detail": (
                            hardware["detail"] if managed_adapter else None
                        ),
                        "gguf_preflight": gguf_status,
                        "package_manifest": package_manifest,
                        "video_adapter": (
                            {
                                "id": video_match[0],
                                "automatic_launch": hardware_ready and artifact_ready,
                            }
                            if video_match
                            else None
                        ),
                        "image_adapter": (
                            {
                                "id": image_match[0],
                                "automatic_launch": hardware_ready and artifact_ready,
                            }
                            if image_match
                            else None
                        ),
                    }
                )
                self._json(200, {"ok": True, "plan": plan})
                return

            if path == "/v1/drive/session":
                DRIVE_SESSION.set(str(payload.get("access_token") or ""))
                self._json(200, {"ok": True, "stored": "memory-only"})
                return

            if path == "/v1/models/cache":
                spec = DriveFileSpec.from_payload(payload)
                info = DRIVE_CACHE.describe(spec)
                self._json(200, {"ok": True, "cache": info})
                return

            if path == "/v1/models/cache/delete":
                file_id = str(payload.get("drive_file_id") or "").strip()
                if not file_id:
                    raise ValueError("drive_file_id is required.")
                snapshot = STATE.snapshot()
                if snapshot.get("source_drive_file_id") == file_id:
                    self._json(409, {"error": "Stop the active model before deleting its cache."})
                    return
                self._json(200, {"ok": True, "result": DRIVE_CACHE.delete_file_id(file_id)})
                return

            if path == "/v1/models/cache/clear":
                snapshot = STATE.snapshot()
                if snapshot.get("source_drive_file_id") and snapshot.get("phase") != "idle":
                    self._json(409, {"error": "Stop the active model or download before clearing the cache."})
                    return
                self._json(200, {"ok": True, "result": DRIVE_CACHE.delete_all()})
                return

            if path == "/v1/models/inspect":
                spec = DriveFileSpec.from_payload(payload)
                model_path = DRIVE_CACHE.cached_path(spec)
                if not model_path:
                    self._json(
                        409,
                        {
                            "error": "Model is not cached yet. Start it once to download from Drive.",
                            "cache": DRIVE_CACHE.describe(spec),
                        },
                    )
                    return
                self._json(200, {"ok": True, "gguf": inspect_gguf(model_path)})
                return

            if path == "/v1/models/start":
                spec = DriveFileSpec.from_payload(payload)
                if Path(spec.name).suffix.lower() != ".gguf":
                    raise ValueError("Direct llama.cpp launch currently supports GGUF only.")

                cache_state = DRIVE_CACHE.describe(spec)
                preflight = gguf_preflight(
                    DRIVE_CACHE.root,
                    expected_bytes=spec.size,
                    partial_bytes=int(cache_state.get("partial_bytes") or 0),
                    threads=MODEL_THREADS,
                )
                if not preflight["ok"]:
                    raise RuntimeError("GGUF 启动前检查失败：" + str(preflight["reason"]))

                model_name = str(payload.get("display_name") or payload.get("name") or spec.name)
                relative_path = str(payload.get("relative_path") or spec.name)
                token = DRIVE_SESSION.get()
                with GENERATION_LOCK:
                    if EDIT.snapshot().get("running"):
                        raise ValueError("请先停止 2511 编辑任务。")
                    result = STATE.start_drive(
                        spec,
                        model_name,
                        relative_path,
                        token,
                    )
                self._json(
                    202,
                    {
                        "ok": True,
                        "model": result["model"],
                        "phase": result["phase"],
                        "ready": result["ready"],
                        "downloaded_bytes": result["downloaded_bytes"],
                        "download_total_bytes": result["download_total_bytes"],
                        "download_progress": result["download_progress"],
                        "cache_file": result["cache_file"],
                        "preflight": preflight,
                    },
                )
                return

            if path == "/v1/image/generate":
                with GENERATION_LOCK:
                    if EDIT.snapshot().get("running"):
                        raise ValueError("请先停止 2511 编辑任务。")
                    if VIDEO.snapshot().get("running"):
                        raise RuntimeError("已有视频任务正在使用 ComfyUI，请先等待或停止视频任务。")
                    result = IMAGE.start(payload)
                self._json(202, {"ok": True, "image": result})
                return

            if path == "/v1/image/stop":
                self._json(200, {"ok": True, "image": IMAGE.stop()})
                return

            if path == "/v1/video/generate":
                with GENERATION_LOCK:
                    if EDIT.snapshot().get("running"):
                        raise ValueError("请先停止 2511 编辑任务。")
                    if IMAGE.snapshot().get("running"):
                        raise RuntimeError("已有图像任务正在使用 ComfyUI，请先等待或停止图像任务。")
                    result = VIDEO.start(payload)
                self._json(202, {"ok": True, "video": result})
                return

            if path == "/v1/video/stop":
                self._json(200, {"ok": True, "video": VIDEO.stop()})
                return

            if path == "/v1/models/stop":
                STATE.stop()
                self._json(200, {"ok": True})
                return

            self._json(404, {"error": "Not found."})
        except FileNotFoundError as error:
            self._json(404, {"error": str(error)})
        except (ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            STATE.append_log("Bridge error: " + repr(error))
            self._json(500, {"error": str(error)})


def main() -> None:
    print("Drive Model Local Runtime v0.17")
    print(f"Bridge: http://{HOST}:{BRIDGE_PORT}")
    print("Drive source: Google Drive API (no desktop mount required)")
    print("Cache root:", DRIVE_CACHE.root)
    print("llama-server:", LLAMA_SERVER_PATH)
    print("llama-server found:", bool(resolve_llama_server()))
    print("CPU threads:", MODEL_THREADS)
    print("GPU layers:", MODEL_GPU_LAYERS)
    print("Load mode:", MODEL_LOAD_MODE)
    print("Ready warning:", f"{MODEL_READY_WARN_SECONDS:.0f}s")
    print("Allowed origins:", ", ".join(sorted(ALLOWED_ORIGINS)))
    print("Remote auth:", "enabled" if REMOTE_TOKEN else "disabled")

    server = ThreadingHTTPServer((HOST, BRIDGE_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        PACKAGE.stop()
        TASK.stop()
        IMAGE.shutdown()
        VIDEO.shutdown()
        STATE.stop()
        server.server_close()


if __name__ == "__main__":
    main()
