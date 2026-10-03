"""Drive-package adapter for fixed, non-explicit Qwen 2511 scene edits."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
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
MAX_IMAGE_PIXELS = 12_000_000


def image_dimensions(data: bytes, suffix: str) -> tuple[int, int]:
    """Check bounded image containers before starting a worker (Pillow decodes there)."""
    width = height = 0
    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) >= 45 and data[8:16] == b"\0\0\0\rIHDR" and data[-12:] == b"\0\0\0\0IEND\xaeB`\x82":
            width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    elif suffix == ".jpg" and data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                break
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0x01, *range(0xD0, 0xD8)}:
                continue
            if marker in {0xD9, 0xDA} or offset + 2 > len(data):
                break
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 8:
                height, width = int.from_bytes(data[offset + 3:offset + 5], "big"), int.from_bytes(data[offset + 5:offset + 7], "big")
                break
            offset += length
    elif suffix == ".webp" and len(data) >= 25 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if int.from_bytes(data[4:8], "little") + 8 == len(data):
            chunk, length, body = data[12:16], int.from_bytes(data[16:20], "little"), data[20:]
            if length <= len(body):
                if chunk == b"VP8X" and length >= 10:
                    width, height = 1 + int.from_bytes(body[4:7], "little"), 1 + int.from_bytes(body[7:10], "little")
                elif chunk == b"VP8L" and length >= 5 and body[0] == 0x2F:
                    bits = int.from_bytes(body[1:5], "little")
                    width, height = 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
                elif chunk == b"VP8 " and length >= 10 and body[3:6] == b"\x9d\x01\x2a":
                    width, height = int.from_bytes(body[6:8], "little") & 0x3FFF, int.from_bytes(body[8:10], "little") & 0x3FFF
    if width < 1 or height < 1:
        raise ValueError("参考图格式无效、容器不完整或缺少尺寸。")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("参考图超过 1200 万像素。")
    return width, height


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
    image_dimensions(data, types[header])
    return data, types[header]


def completed_manifest(actual: Path, package_path: str) -> dict[str, Path]:
    """Validate materializer provenance, file sizes and containment without rereading GB weights."""
    manifest_path = actual / ".jvust-package.json"
    if not manifest_path.is_file() or not manifest_path.resolve().is_relative_to(actual):
        raise ValueError("模型包缺少受管理的已完成清单。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("package_path") != package_path:
        raise ValueError("模型包完成清单与所选目录不一致。")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("模型包完成清单为空。")
    files: dict[str, Path] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("模型包完成清单条目无效。")
        name, size = entry.get("relative_path"), entry.get("size")
        if not isinstance(name, str) or not name or "\\" in name or ":" in name or any(part in {"", ".", ".."} for part in name.split("/")):
            raise ValueError("模型包完成清单路径无效。")
        path = actual.joinpath(*name.split("/"))
        if name in files or not path.resolve().is_relative_to(actual):
            raise ValueError("模型包完成清单包含重复或越界路径。")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not path.is_file() or path.stat().st_size != size:
            raise ValueError("模型包文件缺失或大小与完成清单不符：" + name)
        files[name] = path
    return files


def pipeline_directory(package_root: Path, state: dict) -> Path:
    """Resolve only the completed materializer package, not arbitrary paths."""
    if state.get("phase") != "complete" or state.get("running"):
        raise ValueError("请先完成所选 2511 模型包的本地准备。")
    package_path = str(state.get("package_path") or "")
    if not any(re.sub(r"[^a-z0-9]", "", part.lower()) == "qwenimageedit2511" for part in package_path.split("/")):
        raise ValueError("所选目录不是 Qwen-Image-Edit-2511 模型包。")
    expected = (package_root / package_cache_key(package_path)).resolve()
    actual = Path(str(state.get("package_dir") or "")).resolve()
    if actual != expected or not actual.is_relative_to(package_root.resolve()):
        raise ValueError("模型包不在受管理的缓存目录内。")
    files = completed_manifest(actual, package_path)
    for candidate in (actual, actual / "model"):
        index = candidate / "model_index.json"
        if not index.is_file():
            continue
        for path in candidate.rglob("*"):
            if not path.resolve().is_relative_to(actual):
                raise ValueError("模型目录不允许引用缓存目录之外的文件。")
            if path.is_file() and path.suffix.lower() in {".json", ".txt", ".model", ".safetensors", ".bin"} and path != actual / ".jvust-package.json" and path.relative_to(actual).as_posix() not in files:
                raise ValueError("模型文件未记录在完成清单中：" + path.relative_to(actual).as_posix())
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
                for name in shards.values():
                    if not isinstance(name, str) or not name.endswith(".safetensors") or any(char in name for char in ("/", "\\", ":")):
                        raise ValueError("模型分片索引必须引用同组件内的 safetensors 文件。")
                    shard = (shard_index.parent / name).resolve()
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
        self.package_path = ""
        self.operation = ""
        self.exit_status: int | None = None
        self.error: str | None = None
        self.output: Path | None = None
        self.logs: deque[str] = deque(maxlen=30)

    def snapshot(self) -> dict:
        with self.lock:
            return {"adapter": "qwen_image_edit_2511", "job_id": self.job_id, "phase": self.phase,
                    "package_path": self.package_path, "operation": self.operation, "exit_status": self.exit_status,
                    "running": bool(self.thread and self.thread.is_alive()), "error": self.error,
                    "output_ready": bool(self.phase == "complete" and self.output and self.output.is_file()),
                    "logs": list(self.logs)}

    def environment(self) -> dict:
        try:
            result = subprocess.run([python_path(), "-u", "-I", str(worker_path()), "--preflight"],
                                    capture_output=True, text=True, encoding="utf-8", timeout=45,
                                    env=offline_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return {"supported": False, "detail": "编辑环境检查失败或超时。", "error": str(error), "exit_status": None}
        try:
            state = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            state = {"supported": False, "detail": "编辑环境检查失败。", "error": result.stderr[-1500:]}
        if not isinstance(state, dict):
            state = {"supported": False, "detail": "编辑环境返回了无效状态。"}
        state["supported"] = state.get("supported") is True and result.returncode == 0
        return {**state, "exit_status": result.returncode}

    def start(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("编辑请求必须是 JSON 对象。")
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
            self.package_path = state["package_path"]
            self.operation = operation
            self.exit_status = None
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
                self.exit_status = code
                if self.cancel.is_set():
                    self.phase = "cancelled"
                elif code or not self.output or not self.output.is_file() or self.output.stat().st_size == 0:
                    self.phase = "failed"
                    self.error = f"编辑进程退出 {code}：" + "\n".join(self.logs)[-1500:]
                else:
                    # Never expose a crash log or an arbitrary binary as a PNG result.
                    with self.output.open("rb") as handle:
                        header = handle.read(33)
                        if self.output.stat().st_size >= 45:
                            handle.seek(-12, 2)
                            footer = handle.read(12)
                        else:
                            footer = b""
                    if not header.startswith(b"\x89PNG\r\n\x1a\n") or header[8:16] != b"\0\0\0\rIHDR" or footer != b"\0\0\0\0IEND\xaeB`\x82":
                        raise ValueError("编辑进程输出不是有效 PNG。")
                    self.phase = "complete"
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.phase = "cancelled" if self.cancel.is_set() else "failed"
        finally:
            if process:
                if process.poll() is None:
                    self._terminate(process)
                if process.stdout:
                    process.stdout.close()
            try:
                source.unlink(missing_ok=True)
                if self.phase != "complete" and self.output:
                    self.output.unlink(missing_ok=True)
            except OSError as error:
                with self.lock:
                    self.logs.append("cleanup: " + str(error))
            with self.lock:
                self.process = None

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except ProcessLookupError:
            pass

    def stop(self, job_id: str | None = None) -> dict:
        with self.lock:
            if job_id is not None and (not job_id or job_id != self.job_id):
                raise ValueError("不能停止不匹配的编辑任务。")
            current_job = self.job_id
            self.cancel.set()
            process = self.process
            thread = self.thread
            if thread and thread.is_alive():
                self.phase = "cancelling"
        if process and process.poll() is None:
            self._terminate(process)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=6)
        with self.lock:
            if self.job_id == current_job and not (thread and thread.is_alive()) and self.phase not in {"idle", "complete", "failed"}:
                self.phase = "cancelled"
        return self.snapshot()

    def output_file(self, job_id: str) -> Path:
        with self.lock:
            if not job_id or job_id != self.job_id:
                raise ValueError("未知编辑任务。")
            if self.phase != "complete" or not self.output or not self.output.is_file():
                raise FileNotFoundError("编辑结果尚未就绪。")
            return self.output
