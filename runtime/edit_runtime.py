"""Drive-package adapter for fixed, non-explicit Qwen 2511 scene edits."""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

try:
    from .edit_worker import PRESETS
    from .package_runtime import package_cache_key
except ImportError:
    from edit_worker import PRESETS
    from package_runtime import package_cache_key

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def decode_reference(value: Any) -> tuple[bytes, str]:
    """Accept only bounded inline PNG/JPEG/WebP, never a URL or local path."""
    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 128:
        raise ValueError("参考图缺失或超过 8 MB。")
    header, separator, encoded = value.partition(",")
    types = {"data:image/png;base64": ".png", "data:image/jpeg;base64": ".jpg", "data:image/webp;base64": ".webp"}
    if not separator or header not in types:
        raise ValueError("参考图只支持 PNG、JPEG 或 WebP。")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("参考图 Base64 无效。") from error
    valid = (
        header == "data:image/png;base64" and data.startswith(b"\x89PNG\r\n\x1a\n")
        or header == "data:image/jpeg;base64" and data.startswith(b"\xff\xd8\xff")
        or header == "data:image/webp;base64" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    )
    if not valid or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("参考图内容与图片类型不匹配。")
    return data, types[header]


def pipeline_directory(package_root: Path, state: dict) -> Path:
    """Resolve only the completed materializer package, not arbitrary paths."""
    if state.get("phase") != "complete" or state.get("running"):
        raise ValueError("请先完成所选 2511 模型包的本地准备。")
    expected = (package_root / package_cache_key(str(state.get("package_path") or ""))).resolve()
    actual = Path(str(state.get("package_dir") or "")).resolve()
    if actual != expected or not actual.is_relative_to(package_root.resolve()):
        raise ValueError("模型包不在受管理的缓存目录内。")
    if not (actual / ".jvust-package.json").is_file():
        raise ValueError("模型包缺少已完成清单。")
    for candidate in (actual, actual / "model"):
        index = candidate / "model_index.json"
        if not index.is_file():
            continue
        if not index.resolve().is_relative_to(actual):
            raise ValueError("模型配置不允许引用缓存目录之外的文件。")
        config = json.loads(index.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or config.get("_class_name") != "QwenImageEditPlusPipeline":
            raise ValueError("不是 QwenImageEditPlusPipeline 完整包。")
        required = ["transformer/config.json", "text_encoder/config.json", "vae/config.json", "tokenizer/tokenizer_config.json", "scheduler/scheduler_config.json", "processor/preprocessor_config.json"]
        missing = [name for name in required if not (candidate / name).is_file()]
        if any(not (candidate / name).resolve().is_relative_to(actual) for name in required):
            raise ValueError("模型配置不允许引用缓存目录之外的文件。")
        for component in ("transformer", "text_encoder", "vae"):
            weights = list((candidate / component).glob("*.safetensors"))
            if any(not path.resolve().is_relative_to(actual) for path in weights):
                raise ValueError("模型权重不允许引用缓存目录之外的文件。")
            if not weights or any(p.stat().st_size == 0 for p in weights):
                missing.append(component + "/*.safetensors")
            for shard_index in (candidate / component).glob("*.safetensors.index.json"):
                if not shard_index.resolve().is_relative_to(actual):
                    raise ValueError("分片索引不允许引用缓存目录之外的文件。")
                index_data = json.loads(shard_index.read_text(encoding="utf-8"))
                shards = index_data.get("weight_map", {}) if isinstance(index_data, dict) else {}
                if not isinstance(shards, dict) or not shards:
                    missing.append(str(shard_index.relative_to(candidate)))
                    continue
                for name in set(shards.values()):
                    shard = (shard_index.parent / str(name)).resolve()
                    if not shard.is_relative_to(candidate.resolve()) or not shard.is_file() or shard.stat().st_size == 0:
                        missing.append(component + "/" + str(name))
        if missing:
            raise ValueError("模型包文件不完整：" + ", ".join(missing))
        return candidate.resolve()
    raise ValueError("未找到 model_index.json；请扫描完整的 2511/model 目录。")


def worker_path() -> Path:
    """Support both source and the packaged Windows bridge."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) / "edit_worker.py" if bundled else Path(__file__).with_name("edit_worker.py")


def python_path() -> str:
    configured = os.environ.get("MODEL_DIFFUSERS_PYTHON", "").strip()
    value = configured or (shutil.which("python") if getattr(sys, "frozen", False) else sys.executable)
    if not value or not Path(value).is_file():
        raise ValueError("请配置 MODEL_DIFFUSERS_PYTHON 指向真实的编辑环境 Python。")
    return str(Path(value).resolve())


def offline_environment() -> dict[str, str]:
    # Drive credentials are never inherited by an inference subprocess.
    env = {key: value for key, value in os.environ.items() if not any(word in key.upper() for word in ("TOKEN", "SECRET", "API_KEY"))}
    return {**env, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}


class EditRuntime:
    """Own one cancellable worker; leave Drive downloads to PackageRuntime."""
    def __init__(self, package: Any) -> None:
        self.package = package
        self.root = (package.root.parent / "edit-2511").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.cancel = threading.Event()
        self.phase = "idle"
        self.job_id = ""
        self.error: str | None = None
        self.output: Path | None = None
        self.logs: deque[str] = deque(maxlen=30)

    def snapshot(self) -> dict:
        with self.lock:
            return {"adapter": "qwen_image_edit_2511", "job_id": self.job_id, "phase": self.phase,
                    "running": bool(self.thread and self.thread.is_alive()), "error": self.error,
                    "output_ready": bool(self.phase == "complete" and self.output and self.output.is_file()),
                    "logs": list(self.logs)}

    def environment(self) -> dict:
        result = subprocess.run([python_path(), "-u", "-I", str(worker_path()), "--preflight"],
                                capture_output=True, text=True, encoding="utf-8", timeout=45,
                                env=offline_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            state = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            state = {"supported": False, "detail": "编辑环境检查失败。", "error": result.stderr[-1500:]}
        if result.returncode:
            state["supported"] = False
        return {**state, "exit_status": result.returncode}

    def start(self, payload: dict) -> dict:
        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in PRESETS or any(key in payload for key in ("prompt", "instruction", "negative_prompt")):
            raise ValueError("仅支持保留主体和衣着的背景、光线预设。")
        if payload.get("model_id") != "qwen_image_edit_2511":
            raise ValueError("请先选择 Qwen-Image-Edit-2511。")
        state = self.package.snapshot()
        if payload.get("package_path") != state.get("package_path"):
            raise ValueError("已准备模型包与所选模型不一致。")
        model_dir = pipeline_directory(self.package.root, state)
        data, suffix = decode_reference(payload.get("reference_image"))
        values = [payload.get("seed", 0), payload.get("steps", 40)]
        try:
            seed, steps = (int(value) for value in values)
        except (TypeError, ValueError) as error:
            raise ValueError("Seed 和 Steps 必须是整数。") from error
        if any(isinstance(value, bool) or str(integer) != str(value).strip() for value, integer in zip(values, (seed, steps))):
            raise ValueError("Seed 和 Steps 必须是整数。")
        if not 0 <= seed <= 0xFFFFFFFF or not 1 <= steps <= 50:
            raise ValueError("Seed 或 Steps 超出允许范围。")
        executable = python_path()
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("已有 2511 编辑任务正在运行。")
            self.cancel.clear()
            self.job_id = secrets.token_hex(12)
            self.error = None
            self.output = self.root / (self.job_id + ".png")
            source = self.root / (self.job_id + "-input" + suffix)
            source.write_bytes(data)
            self.logs.clear()
            self.phase = "starting"
            command = [executable, "-u", "-I", str(worker_path()), "--model-dir", str(model_dir),
                       "--input", str(source), "--output", str(self.output), "--operation", operation,
                       "--seed", str(seed), "--steps", str(steps)]
            self.thread = threading.Thread(target=self._run, args=(command, source), daemon=True)
            self.thread.start()
            return self.snapshot()

    def _run(self, command: list[str], source: Path) -> None:
        process = None
        try:
            with self.lock:
                if self.cancel.is_set():
                    self.phase = "cancelled"
                    return
                self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                text=True, encoding="utf-8", errors="replace", env=offline_environment(),
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                process = self.process
            if process.stdout:
                for line in process.stdout:
                    with self.lock:
                        self.logs.append(line.strip())
                        if line.startswith(("loading:", "generating:")) and not self.cancel.is_set():
                            self.phase = line.split(":", 1)[0]
            code = process.wait()
            with self.lock:
                if self.cancel.is_set():
                    self.phase = "cancelled"
                elif code or not self.output or not self.output.is_file() or self.output.stat().st_size == 0:
                    self.phase = "failed"
                    self.error = f"编辑进程退出 {code}：" + "\n".join(self.logs)[-1500:]
                else:
                    self.phase = "complete"
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.phase = "cancelled" if self.cancel.is_set() else "failed"
        finally:
            if process and process.stdout:
                process.stdout.close()
            source.unlink(missing_ok=True)
            with self.lock:
                self.process = None

    def stop(self) -> dict:
        with self.lock:
            self.cancel.set()
            process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)
        with self.lock:
            if self.phase not in {"idle", "complete", "failed"}:
                self.phase = "cancelled"
        return self.snapshot()

    def output_file(self, job_id: str) -> Path:
        with self.lock:
            if not job_id or job_id != self.job_id:
                raise ValueError("未知编辑任务。")
            if self.phase != "complete" or not self.output or not self.output.is_file():
                raise FileNotFoundError("编辑结果尚未就绪。")
            return self.output
