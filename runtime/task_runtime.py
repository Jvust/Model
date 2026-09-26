from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .drive_cache import DriveCache, DriveFileSpec
except ImportError:
    from drive_cache import DriveCache, DriveFileSpec


TASK_PORT = int(os.environ.get("MODEL_TASK_PORT", "8091"))
TASK_THREADS = int(
    os.environ.get(
        "MODEL_TASK_THREADS",
        str(max(1, (os.cpu_count() or 4) - 2)),
    )
)
TASK_GPU_LAYERS = int(os.environ.get("MODEL_TASK_GPU_LAYERS", "0"))
TASK_CONTEXT = int(os.environ.get("MODEL_TASK_CONTEXT", "4096"))
TASK_BATCH = int(os.environ.get("MODEL_TASK_BATCH", str(TASK_CONTEXT)))
TASK_READY_TIMEOUT = float(os.environ.get("MODEL_TASK_READY_TIMEOUT", "300"))

EMBEDDING_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"
EMBEDDING_BYTES = 639_150_592
RERANKER_FILE = "qwen3-reranker-0.6b-q8_0.gguf"
RERANKER_BYTES = 639_153_184

TASK_ADAPTERS = {
    "qwen3_embedding_0_6b": {
        "label": "Qwen3 Embedding 0.6B Q8_0",
        "kind": "embedding",
        "match": (
            "qwen3_embedding_0_6b",
            "qwen3-embedding-0.6b",
            "qwen3 embedding 0.6b",
        ),
        "file_name": EMBEDDING_FILE,
        "expected_bytes": EMBEDDING_BYTES,
        "flags": ("--embedding", "--pooling", "last"),
    },
    "qwen3_reranker_0_6b": {
        "label": "Qwen3 Reranker 0.6B Q8_0",
        "kind": "reranker",
        "match": (
            "qwen3_reranker_0_6b",
            "qwen3-reranker-0.6b",
            "qwen3 reranker 0.6b",
        ),
        "file_name": RERANKER_FILE,
        "expected_bytes": RERANKER_BYTES,
        "flags": ("--embedding", "--reranking", "--pooling", "rank"),
    },
}


def adapter_for(name: str, model_id: str = "", package_path: str = "") -> tuple[str, dict] | None:
    hay = f"{model_id} {name} {package_path}".strip().lower()
    for key, adapter in TASK_ADAPTERS.items():
        if any(token in hay for token in adapter["match"]):
            return key, adapter
    return None


def normalize_file_payload(item: dict) -> dict:
    return {
        "drive_file_id": item.get("drive_file_id") or item.get("id") or "",
        "file_name": item.get("file_name") or item.get("name") or "",
        "size": item.get("size"),
        "md5_checksum": item.get("md5_checksum") or item.get("md5Checksum"),
        "resource_key": item.get("resource_key") or item.get("resourceKey"),
    }


def task_file_spec(payload: dict, adapter: dict) -> DriveFileSpec:
    expected_name = str(adapter["file_name"])
    expected_bytes = int(adapter["expected_bytes"])

    candidates = []
    if isinstance(payload.get("files"), list):
        candidates.extend(item for item in payload["files"] if isinstance(item, dict))
    if payload.get("drive_file_id") or payload.get("id"):
        candidates.append(payload)

    for raw in candidates:
        name = str(raw.get("file_name") or raw.get("name") or "").strip()
        if name.lower() != expected_name.lower():
            continue
        spec = DriveFileSpec.from_payload(normalize_file_payload(raw))
        if spec.size is None:
            raise ValueError(f"{expected_name} 缺少 Drive 文件大小。")
        if int(spec.size) != expected_bytes:
            raise ValueError(
                f"{expected_name} 文件大小不匹配：{spec.size} != {expected_bytes}。"
            )
        return spec

    raise FileNotFoundError(f"Drive 模型包缺少固定 GGUF：{expected_name}")


def inspect_gguf(path: Path) -> dict:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24:
        raise ValueError("GGUF file is too small to contain a complete header.")
    magic, version, tensor_count, metadata_kv_count = struct.unpack("<4sIQQ", header)
    if magic != b"GGUF":
        raise ValueError("GGUF magic header is missing.")
    return {
        "version": int(version),
        "tensor_count": int(tensor_count),
        "metadata_kv_count": int(metadata_kv_count),
        "file_size": int(path.stat().st_size),
    }


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 30.0,
):
    data = None
    headers = {"User-Agent": "JvustModelTask/1.0"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            body = json.loads(raw.decode("utf-8")) if raw else {}
            return int(response.status), body
    except HTTPError as error:
        raw = error.read()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {"error": raw.decode("utf-8", errors="replace")}
        return int(error.code), body


def task_health(port: int = TASK_PORT) -> bool:
    request = Request(f"http://127.0.0.1:{port}/health", method="GET")
    try:
        with urlopen(request, timeout=0.75) as response:
            return int(response.status) == 200
    except (HTTPError, URLError, TimeoutError, socket.timeout, OSError):
        return False


class TaskRuntime:
    def __init__(
        self,
        drive_cache: DriveCache,
        token_provider: Callable[[], str],
        executable_provider: Callable[[], str | None],
    ) -> None:
        self.drive_cache = drive_cache
        self.token_provider = token_provider
        self.executable_provider = executable_provider
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.job_id = 0
        self.adapter_id: str | None = None
        self.kind: str | None = None
        self.model: str | None = None
        self.phase = "idle"
        self.error: str | None = None
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.current_file: str | None = None
        self.downloaded_bytes = 0
        self.download_total_bytes: int | None = None
        self.cache_file: str | None = None
        self.logs: deque[str] = deque(maxlen=200)

    def log(self, message: str) -> None:
        value = str(message).strip()
        if value:
            with self.lock:
                self.logs.append(value)

    def snapshot(self) -> dict:
        with self.lock:
            process = self.process
            running = bool(process and process.poll() is None)
            total = self.download_total_bytes
            progress = (
                min(1.0, self.downloaded_bytes / total)
                if total and total > 0
                else None
            )
            return {
                "running": running,
                "ready": running and self.phase == "ready",
                "phase": self.phase,
                "adapter": self.adapter_id,
                "kind": self.kind,
                "model": self.model,
                "pid": process.pid if running and process else None,
                "port": TASK_PORT,
                "server_url": f"http://127.0.0.1:{TASK_PORT}" if running else None,
                "started_at": self.started_at if running else None,
                "ready_at": self.ready_at if running else None,
                "current_file": self.current_file,
                "downloaded_bytes": self.downloaded_bytes,
                "download_total_bytes": self.download_total_bytes,
                "download_progress": progress,
                "cache_file": self.cache_file,
                "threads": TASK_THREADS,
                "gpu_layers": TASK_GPU_LAYERS,
                "context": TASK_CONTEXT,
                "batch": TASK_BATCH,
                "error": self.error,
                "logs": list(self.logs)[-30:],
                "supported_adapters": [
                    {
                        "id": key,
                        "label": item["label"],
                        "kind": item["kind"],
                        "file_name": item["file_name"],
                        "expected_bytes": item["expected_bytes"],
                    }
                    for key, item in TASK_ADAPTERS.items()
                ],
            }

    def stop(self) -> dict:
        with self.lock:
            self.job_id += 1
            process = self.process
            self.process = None
            self.adapter_id = None
            self.kind = None
            self.model = None
            self.phase = "idle"
            self.error = None
            self.started_at = None
            self.ready_at = None
            self.current_file = None
            self.downloaded_bytes = 0
            self.download_total_bytes = None
            self.cache_file = None

        if process and process.poll() is None:
            self.log("Stopping task llama-server...")
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        return self.snapshot()

    def start(self, payload: dict) -> dict:
        name = str(payload.get("name") or "")
        model_id = str(payload.get("model_id") or "")
        package_path = str(payload.get("package_path") or "")
        matched = adapter_for(name, model_id, package_path)
        if not matched:
            raise ValueError("这个专用任务模型还没有 llama.cpp task adapter。")
        adapter_id, adapter = matched
        spec = task_file_spec(payload, adapter)
        token = self.token_provider()

        self.stop()
        with self.lock:
            job_id = self.job_id
            self.adapter_id = adapter_id
            self.kind = str(adapter["kind"])
            self.model = name or str(adapter["label"])
            self.phase = "downloading"
            self.error = None
            self.current_file = spec.name
            self.download_total_bytes = spec.size

        cached = self.drive_cache.cached_path(spec)
        if cached:
            with self.lock:
                self.downloaded_bytes = cached.stat().st_size
                self.cache_file = cached.name
            self._launch(job_id, cached, adapter)
            return self.snapshot()

        threading.Thread(
            target=self._download_and_launch,
            args=(job_id, spec, adapter, token),
            daemon=True,
        ).start()
        return self.snapshot()

    def _download_and_launch(
        self,
        job_id: int,
        spec: DriveFileSpec,
        adapter: dict,
        token: str,
    ) -> None:
        def progress(received: int, total: int | None) -> None:
            with self.lock:
                if self.job_id != job_id:
                    return
                self.downloaded_bytes = int(received)
                self.download_total_bytes = int(total) if total else None

        try:
            path = self.drive_cache.download(spec, token, progress)
            with self.lock:
                if self.job_id != job_id:
                    return
                self.cache_file = path.name
            self._launch(job_id, path, adapter)
        except Exception as error:
            with self.lock:
                if self.job_id != job_id:
                    return
                self.phase = "failed"
                self.error = str(error)
            self.log("Task model preparation failed: " + repr(error))

    def _launch(self, job_id: int, model_path: Path, adapter: dict) -> None:
        info = inspect_gguf(model_path)
        executable = self.executable_provider()
        if not executable:
            raise FileNotFoundError(
                "llama-server was not found. Configure LLAMA_SERVER_PATH first."
            )

        command = [
            executable,
            "-m",
            str(model_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(TASK_PORT),
            "-t",
            str(TASK_THREADS),
            "-ngl",
            str(TASK_GPU_LAYERS),
            "--ctx-size",
            str(TASK_CONTEXT),
            "--batch-size",
            str(TASK_BATCH),
            "--ubatch-size",
            str(TASK_BATCH),
            "--parallel",
            "1",
            "--no-webui",
            *tuple(adapter["flags"]),
        ]

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        self.log(
            f"Launching task server: kind={adapter['kind']} "
            f"threads={TASK_THREADS} gpu_layers={TASK_GPU_LAYERS}"
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
            self.phase = "loading"
            self.started_at = time.time()
            self.ready_at = None
            self.error = None

        self.log(
            f"GGUF verified: v{info['version']} tensors={info['tensor_count']}"
        )
        threading.Thread(target=self._read_output, args=(process,), daemon=True).start()
        threading.Thread(target=self._monitor, args=(job_id, process), daemon=True).start()

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout:
            for line in process.stdout:
                self.log(line)
        code = process.poll()
        with self.lock:
            if self.process is process and self.phase == "ready":
                self.phase = "failed"
                self.error = f"Task llama-server exited with code {code}."

    def _monitor(self, job_id: int, process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + TASK_READY_TIMEOUT
        while process.poll() is None and time.monotonic() < deadline:
            if task_health():
                with self.lock:
                    if self.job_id != job_id or self.process is not process:
                        return
                    self.phase = "ready"
                    self.ready_at = time.time()
                    self.error = None
                self.log("Task llama-server is ready.")
                return
            time.sleep(0.5)

        if process.poll() is None:
            process.terminate()
        with self.lock:
            if self.job_id == job_id and self.process is process:
                self.phase = "failed"
                self.error = "Task llama-server readiness timed out."

    def embeddings(self, payload: dict) -> dict:
        with self.lock:
            if self.phase != "ready" or self.kind != "embedding":
                raise RuntimeError("Embedding task model is not ready.")

        raw = payload.get("input", payload.get("texts"))
        if isinstance(raw, str):
            inputs = [raw]
        elif isinstance(raw, list):
            inputs = [str(item) for item in raw]
        else:
            raise ValueError("input/texts must be text or an array of text.")

        inputs = [item.strip() for item in inputs if item.strip()]
        if not inputs or len(inputs) > 64:
            raise ValueError("Embedding input count must be between 1 and 64.")
        if any(len(item) > 16000 for item in inputs):
            raise ValueError("单条 Embedding 文本过长；第一版 task server 限制约 4096 tokens。")

        status, result = json_request(
            f"http://127.0.0.1:{TASK_PORT}/v1/embeddings",
            method="POST",
            payload={"input": inputs},
            timeout=300,
        )
        if status >= 400:
            raise RuntimeError(result.get("error") or f"Embedding server HTTP {status}")
        return result

    def rerank(self, payload: dict) -> dict:
        with self.lock:
            if self.phase != "ready" or self.kind != "reranker":
                raise RuntimeError("Reranker task model is not ready.")

        query = str(payload.get("query") or "").strip()
        documents = payload.get("documents")
        if not query:
            raise ValueError("query is required.")
        if not isinstance(documents, list):
            raise ValueError("documents must be an array.")
        docs = [str(item).strip() for item in documents if str(item).strip()]
        if not docs or len(docs) > 100:
            raise ValueError("documents count must be between 1 and 100.")
        if any(len(query) + len(item) > 16000 for item in docs):
            raise ValueError("单个 query/document 对过长；第一版 task server 限制约 4096 tokens。")

        try:
            top_n = int(payload.get("top_n", len(docs)))
        except (TypeError, ValueError) as exc:
            raise ValueError("top_n must be an integer.") from exc
        top_n = max(1, min(top_n, len(docs)))

        status, result = json_request(
            f"http://127.0.0.1:{TASK_PORT}/v1/rerank",
            method="POST",
            payload={"query": query, "documents": docs, "top_n": top_n},
            timeout=300,
        )
        if status >= 400:
            raise RuntimeError(result.get("error") or f"Reranker server HTTP {status}")
        return result
