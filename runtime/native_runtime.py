"""Drive-first, cancellable OCR / forecasting / Diffusers job manager."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

try:
    from .native_worker import CATALOG, validate_input
    from .package_runtime import normalize_manifest
    from .package_integrity import verify_directory, check_index_manifest, read_json
    from .hardware import disk_status, system_memory_status, nvidia_status
    from .managed_env import ensure_environment, check_cancel
except ImportError:
    from native_worker import CATALOG, validate_input
    from package_runtime import normalize_manifest
    from package_integrity import verify_directory, check_index_manifest, read_json
    from hardware import disk_status, system_memory_status, nvidia_status
    from managed_env import ensure_environment, check_cancel


def validate_manifest(model_id, payload):
    if model_id not in CATALOG:
        raise ValueError("Unsupported model")
    package, files = normalize_manifest(payload)
    if len(files) > 4096:
        raise ValueError("Too many package files")
    paths = {item["relative_path"] for item in files}
    lowered = [path.casefold() for path in paths]
    if len(set(lowered)) != len(lowered):
        raise ValueError("Case-insensitive duplicate package paths")
    for path in paths:
        if path.startswith("/") or "\\" in path or any(part.endswith((".", " ")) for part in path.split("/")):
            raise ValueError("Unsafe Windows package path")
        if path.lower().endswith((".py", ".exe", ".dll", ".bat", ".cmd", ".ps1", ".pkl")):
            raise ValueError("Executable files are not accepted as model assets")
    required = {"config.json"}
    if model_id == "got_ocr2":
        required |= {"preprocessor_config.json", "tokenizer.json"}
    elif model_id == "timesfm_2_0_500m":
        required = {"torch_model.ckpt"}
    elif model_id == "flux2_klein_4b_diffusers":
        required = {"model_index.json", "transformer/config.json", "text_encoder/config.json",
                    "vae/config.json", "scheduler/scheduler_config.json", "tokenizer/tokenizer_config.json"}
        for component in ("transformer", "text_encoder", "vae"):
            if not any(path.startswith(component + "/") and path.endswith(".safetensors") for path in paths):
                raise ValueError(f"Missing {component} safetensors")
    elif model_id.startswith("wan22_"):
        required = {"model_index.json", "transformer/config.json", "transformer_2/config.json", "text_encoder/config.json", "vae/config.json", "scheduler/scheduler_config.json", "tokenizer/tokenizer_config.json"}
        for component in ("transformer", "transformer_2", "text_encoder", "vae"):
            if not any(path.startswith(component + "/") and path.endswith(".safetensors") for path in paths):
                raise ValueError(f"Missing {component} safetensors")
    if not required.issubset(paths):
        raise ValueError("Incomplete model package: " + ", ".join(sorted(required - paths)))
    if model_id != "timesfm_2_0_500m" and not any(path.endswith(".safetensors") for path in paths):
        raise ValueError("Safetensors model weights are required")
    for item in files:
        if item["relative_path"].endswith(".json") and item["spec"].size > 20_000_000:
            raise ValueError("Model metadata exceeds 20 MB")
    return package, files


def verify_package(model_id, directory):
    root = Path(directory).resolve()
    verify_directory(root)
    if model_id == "got_ocr2":
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        if config.get("model_type") != "got_ocr2":
            raise ValueError("GOT-OCR needs the native HF package, not the legacy custom-code checkpoint")
    if model_id == "flux2_klein_4b_diffusers":
        index = read_json(root / "model_index.json")
        config = read_json(root / "transformer" / "config.json")
        if index.get("_class_name") != "Flux2KleinPipeline" or index.get("is_distilled") is not True:
            raise ValueError("Wrong pipeline for FLUX.2 klein")
        # Do not label a 9B or non-distilled package as the curated 4B profile.
        te = read_json(root / "text_encoder" / "config.json")
        if te.get("hidden_size") != 2560 or config.get("guidance_embeds", False):
            raise ValueError("This adapter requires distilled FLUX.2 klein 4B and its Qwen3-4B encoder")
    if model_id.startswith("wan22_"):
        config = json.loads((root / "model_index.json").read_text(encoding="utf-8"))
        expected = "WanImageToVideoPipeline" if model_id == "wan22_i2v_a14b" else "WanPipeline"
        if config.get("_class_name") != expected:
            raise ValueError("Wrong Diffusers pipeline class for this model")


class NativeRuntime:
    def __init__(self, cache, token_provider):
        self.cache, self.token_provider = cache, token_provider
        self.root = cache.root / "native"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.process = None
        self.state = {"phase": "idle", "job_id": None}

    def snapshot(self):
        with self.lock:
            state = dict(self.state)
            state["running"] = bool(self.thread and self.thread.is_alive())
            return state

    def plan(self, payload):
        model_id = str(payload.get("model_id") or "")
        if model_id not in CATALOG:
            raise ValueError("Unsupported native model")
        reasons = []
        try:
            _, files = validate_manifest(model_id, payload)
        except ValueError as error:
            files = []
            reasons.append(str(error))
        info = CATALOG[model_id]
        memory = system_memory_status()
        available = memory.get("available_bytes")
        if available is None or available < info["ram_gib"] * 1024**3:
            reasons.append(f"At least {info['ram_gib']} GiB available RAM is required")
        if info.get("gpu"):
            gpu = nvidia_status()
            vram = int(gpu.get("vram_total_mb") or 0)
            if not gpu.get("detected") or vram < info.get("vram_mb", 16 * 1024):
                reasons.append("NVIDIA CUDA 显存不足，请使用更小运行副本或远程 Runtime")
            if model_id == "flux2_klein_4b_diffusers" and vram < 12 * 1024 and (available is None or available < 24 * 1024**3):
                reasons.append("8GB 显存的 FLUX 分阶段模式需要至少 24 GiB 当前可用系统 RAM")
        remaining = 0
        for item in files:
            spec = item["spec"]
            if not self.cache.cached_path(spec):
                description = self.cache.describe(spec)
                partial = max(description.get("partial_bytes") or 0, description.get("candidate_bytes") or 0)
                remaining += max(0, int(spec.size) - int(partial))
        disk = disk_status(self.root)
        # Dependencies and temporary outputs require headroom even for cached weights.
        required = remaining + (12 if info.get("gpu") else 5) * 1024**3
        if disk["free_bytes"] < required:
            reasons.append("Insufficient free disk for remaining weights, environment and output")
        return {"model_id": model_id, "ready": not reasons, "reasons": reasons, "remaining_bytes": remaining, "required_free_bytes": required, "memory": memory, "disk": disk, "model_execution_verified": False}

    def start(self, payload):
        model_id = str(payload.get("model_id") or "")
        task_input = validate_input(model_id, payload.get("input") or {})
        plan = self.plan(payload)
        if not plan["ready"]:
            raise ValueError("; ".join(plan["reasons"]))
        package, files = validate_manifest(model_id, payload)
        token = self.cache.access_token(payload, self.token_provider)  # memory only, never written into worker inputs
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("A native job is already active or cancelling")
            self.cancel = threading.Event()
            job_id = uuid.uuid4().hex
            self.state = {"phase": "starting", "job_id": job_id, "model_id": model_id, "started_at": time.time(), "error": None}
            self.thread = threading.Thread(target=self._run, args=(job_id, model_id, package, files, task_input, token), daemon=True)
            self.thread.start()
        return self.snapshot()

    def stop(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.cancel.set()
                self.state["phase"] = "cancelling"
        return self.snapshot()

    def phase(self, phase, detail=""):
        check_cancel(self.cancel)
        with self.lock:
            self.state.update(phase=phase, detail=detail)

    def _materialize(self, model_id, files):
        identity = [{"path": item["relative_path"], "id": item["spec"].file_id, "size": item["spec"].size, "md5": item["spec"].md5_checksum} for item in files]
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
        root = self.root / "packages" / f"{model_id}-{digest}"
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("Symlink package roots are not allowed")
        return root

    def prepare(self, payload, cancel, progress=lambda message: None):
        model_id = str(payload.get("model_id") or "")
        plan = self.plan(payload)
        if not plan["ready"]:
            raise ValueError("; ".join(plan["reasons"]))
        _, files = validate_manifest(model_id, payload)
        token = self.cache.access_token(payload, self.token_provider)
        self.cancel = cancel
        directory, interpreter = self._prepare_files(model_id, files, token, progress)
        return {"directory": str(directory), "interpreter": str(interpreter)}

    def _prepare_files(self, model_id, files, token, progress):
        directory = self._materialize(model_id, files)
        # Fail before large transfers if the index names absent source shards.
        paths = {item["relative_path"] for item in files}
        for item in files:
            if item["relative_path"].endswith(".index.json"):
                spec = item["spec"]
                cached = self.cache.cached_path(spec) or self.cache.download(spec, token,
                    lambda received, total: check_cancel(self.cancel))
                check_index_manifest(item["relative_path"], read_json(cached), paths)
        # Configs first, then weights. Preserve directory layout and cache identity.
        for number, item in enumerate(sorted(files, key=lambda item: not item["relative_path"].endswith(".json")), 1):
            progress(f"准备权重 {number}/{len(files)} {item['relative_path']}")
            spec = item["spec"]
            cached = self.cache.cached_path(spec)
            if cached is None:
                def download_progress(received, total):
                    check_cancel(self.cancel)
                    with self.lock:
                        self.state.update(file_bytes=received, file_total_bytes=total)
                cached = self.cache.download(spec, token, download_progress)
            target = directory.joinpath(*item["relative_path"].split("/"))
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Package path escapes destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.is_symlink() or not os.path.samefile(cached, target):
                    raise ValueError("Cached package identity changed unexpectedly")
            else:
                os.link(cached, target)  # Same cache volume, no duplicate multi-GB copies.
        token = None
        verify_package(model_id, directory)
        interpreter = ensure_environment(self.root, model_id, self.cancel, progress)
        return directory, interpreter

    def _run(self, job_id, model_id, package, files, task_input, token):
        output = self.root / "jobs" / job_id
        output.mkdir(parents=True, exist_ok=True)
        try:
            directory, interpreter = self._prepare_files(model_id, files, token, lambda text: self.phase("preparing", text))
            self.phase("running", CATALOG[model_id]["label"])
            worker = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "native_worker.py"
            if not worker.is_file():
                worker = Path(__file__).parent / "native_worker.py"
            env = {key: value for key, value in os.environ.items() if not any(word in key.upper() for word in ("TOKEN", "SECRET", "API_KEY"))}
            env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
            request = {"model_id": model_id, "model_dir": str(directory), "output_dir": str(output), "input": task_input}
            with (output / "worker.log").open("wb") as log:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                self.process = subprocess.Popen([str(interpreter), str(worker)], stdin=subprocess.PIPE, stdout=log, stderr=log, env=env, creationflags=flags)
                self.process.stdin.write(json.dumps(request).encode("utf-8"))
                self.process.stdin.close()
                started = time.monotonic()
                while self.process.poll() is None:
                    check_cancel(self.cancel)
                    if time.monotonic() - started > 7200:
                        raise TimeoutError("Inference exceeded the two-hour job limit")
                    time.sleep(0.2)
                if self.process.returncode:
                    raise RuntimeError("Inference failed; inspect the private worker.log in this job's output directory")
            check_cancel(self.cancel)
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            if result.get("model_id") != model_id:
                raise ValueError("Worker result model identity mismatch")
            with self.lock:
                self.state.update(phase="complete", finished_at=time.time(), result_ready=True)
        except Exception as error:
            with self.lock:
                self.state.update(phase="cancelled" if self.cancel.is_set() else "failed", error=str(error), finished_at=time.time())
        finally:
            token = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(5)
            self.process = None

    def result_path(self, job_id, media=False):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
            raise ValueError("Invalid job ID")
        path = self.root / "jobs" / job_id / ("result.json")
        if not path.resolve().is_relative_to(self.root.resolve()) or not path.is_file():
            raise FileNotFoundError("Result does not exist")
        if media:
            metadata = read_json(path)
            filename = metadata.get("file")
            if filename not in {"video.mp4", "image.png"}:
                raise FileNotFoundError("No media output")
            path = path.parent / filename
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError("Media output missing")
        return path
