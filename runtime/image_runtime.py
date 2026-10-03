from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from collections import deque
from pathlib import Path
from typing import Callable
from urllib.parse import quote

try:
    from .drive_cache import DriveCache, DriveFileSpec, default_cache_root
    from .hardware import managed_comfy_preflight
    from .video_runtime import COMFY_BASE, json_request
except ImportError:
    from drive_cache import DriveCache, DriveFileSpec, default_cache_root
    from hardware import managed_comfy_preflight
    from video_runtime import COMFY_BASE, json_request


IMAGE_TIMEOUT_SECONDS = int(os.environ.get("MODEL_IMAGE_TIMEOUT_SECONDS", "3600"))
IMAGE_ROOT = Path(
    os.environ.get("MODEL_IMAGE_ROOT", str(default_cache_root() / "image"))
).expanduser().resolve()
IMAGE_OUTPUT_ROOT = IMAGE_ROOT / "outputs"

PONY_CHECKPOINT = "ponyDiffusionV6XL_v6StartWithThisOne.safetensors"
PONY_MIN_BYTES = 6_000_000_000
QWEN_UNET = "qwen-image-2.1-Q4_K_M.gguf"
QWEN_TEXT_ENCODER = "qwen3vl_8b_int8_convrot.safetensors"
QWEN_VAE = "qwen_image_2.1_vae_bf16.safetensors"
FLUX2_KLEIN_FP8 = "flux-2-klein-4b-fp8.safetensors"
FLUX2_TEXT_ENCODER = "qwen_3_4b.safetensors"
FLUX2_VAE = "flux2-vae.safetensors"

COMFY_GGUF_ARCHIVE_URL = (
    "https://github.com/city96/ComfyUI-GGUF/archive/refs/heads/main.zip"
)
COMFY_GGUF_ARCHIVE_NAME = "ComfyUI-GGUF-main.zip"

ADAPTERS = {
    "pony_diffusion_v6_xl": {
        "label": "Pony Diffusion V6 XL",
        "match": (
            "pony_diffusion_v6_xl",
            "pony diffusion v6 xl",
            "pony-diffusion-v6-xl",
            "snupihog__pony_diffusion_v6_xl",
        ),
        "workflow_kind": "pony_sdxl",
        "artifacts": {
            "checkpoint": {
                "name": PONY_CHECKPOINT,
                "min_bytes": PONY_MIN_BYTES,
                "directories": ("checkpoints",),
            },
        },
        "min_vram_mb": 8 * 1024,
        "min_disk_free_gb": 10.0,
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 28,
            "cfg": 5.0,
            "clip_skip": 2,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "size_step": 64,
            "min_size": 512,
        },
    },
    "qwen_image_2_1_int8": {
        "label": "Qwen-Image-2.1 INT8 / GGUF",
        "match": (
            "qwen_image_2_1_int8",
            "qwen-image-2.1",
            "qwen image 2.1",
        ),
        "workflow_kind": "qwen_image_2_1",
        "requires_comfy_gguf": True,
        "required_nodes": (
            "UnetLoaderGGUF",
            "CLIPLoader",
            "VAELoader",
            "TextEncodeQwenImage21",
            "EmptyLatentImage",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        ),
        "required_node_values": {
            "CLIPLoader": {"type": "qwen_image"},
        },
        "artifacts": {
            "unet": {
                "name": QWEN_UNET,
                "min_bytes": 4_500_000_000,
                "directories": ("unet", "diffusion_models"),
            },
            "clip": {
                "name": QWEN_TEXT_ENCODER,
                "min_bytes": 9_000_000_000,
                "directories": ("text_encoders",),
            },
            "vae": {
                "name": QWEN_VAE,
                "min_bytes": 650_000_000,
                "directories": ("vae",),
            },
        },
        "min_vram_mb": 14 * 1024,
        "min_disk_free_gb": 18.0,
        "defaults": {
            "width": 768,
            "height": 768,
            "steps": 20,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "size_step": 32,
            "min_size": 256,
            "resolution": 1024,
        },
    },    "flux2_klein_4b_fp8": {
        "label": "FLUX.2 Klein 4B FP8",
        "match": (
            "flux2_klein_4b_fp8",
            "flux.2-klein-4b-fp8",
            "flux2-klein-4b-fp8",
            "flux2 klein 4b fp8",
            "black-forest-labs__flux.2-klein-4b-fp8",
        ),
        "workflow_kind": "flux2_klein_4b",
        "required_nodes": (
            "UNETLoader",
            "CLIPLoader",
            "VAELoader",
            "CLIPTextEncode",
            "ConditioningZeroOut",
            "RandomNoise",
            "KSamplerSelect",
            "Flux2Scheduler",
            "CFGGuider",
            "EmptyFlux2LatentImage",
            "SamplerCustomAdvanced",
            "VAEDecode",
            "SaveImage",
        ),
        "required_node_values": {
            "CLIPLoader": {"type": "flux2"},
        },
        "artifacts": {
            "unet": {
                "name": FLUX2_KLEIN_FP8,
                "min_bytes": 4_000_000_000,
                "expected_bytes": 4_070_624_520,
                "directories": ("diffusion_models",),
            },
            "clip": {
                "name": FLUX2_TEXT_ENCODER,
                "min_bytes": 8_000_000_000,
                "expected_bytes": 8_044_982_048,
                "directories": ("text_encoders",),
            },
            "vae": {
                "name": FLUX2_VAE,
                "min_bytes": 330_000_000,
                "expected_bytes": 336_213_556,
                "directories": ("vae",),
            },
        },
        "min_vram_mb": 10 * 1024,
        "min_disk_free_gb": 15.0,
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 4,
            "cfg": 1.0,
            "sampler_name": "euler",
            "size_step": 32,
            "min_size": 256,
        },
    },
}


def adapter_for(name: str, model_id: str = "", package_path: str = "") -> tuple[str, dict] | None:
    hay = f"{model_id} {name} {package_path}".strip().lower()
    for key, adapter in ADAPTERS.items():
        if any(token in hay for token in adapter["match"]):
            return key, adapter
    return None


def managed_comfy_hardware(adapter: dict | None = None) -> dict:
    adapter = adapter or {}
    return managed_comfy_preflight(
        IMAGE_ROOT,
        min_vram_mb=int(adapter.get("min_vram_mb") or 0),
        min_disk_free_gb=float(adapter.get("min_disk_free_gb") or 10.0),
    )


def _normalize_file_payload(item: dict) -> dict:
    return {
        "drive_file_id": item.get("drive_file_id") or item.get("id") or "",
        "file_name": item.get("file_name") or item.get("name") or "",
        "size": item.get("size"),
        "md5_checksum": item.get("md5_checksum") or item.get("md5Checksum"),
        "resource_key": item.get("resource_key") or item.get("resourceKey"),
    }


def artifact_specs(payload: dict, adapter: dict) -> dict[str, DriveFileSpec]:
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("图像任务缺少 Drive 模型文件列表；请重新扫描模型源。")

    declared = adapter.get("artifacts") or {}
    if not isinstance(declared, dict) or not declared:
        raise ValueError("图像适配器没有声明固定模型文件。")

    by_name = {}
    for raw in files:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("file_name") or raw.get("name") or "").strip()
        if name:
            by_name[name.lower()] = raw

    result: dict[str, DriveFileSpec] = {}
    missing = []
    for role, requirement in declared.items():
        expected = str(requirement.get("name") or "").strip()
        raw = by_name.get(expected.lower())
        if not raw:
            missing.append(expected)
            continue

        spec = DriveFileSpec.from_payload(_normalize_file_payload(raw))
        minimum = int(requirement.get("min_bytes") or 0)
        exact = int(requirement.get("expected_bytes") or 0)
        if spec.size is None:
            raise ValueError(f"{expected} 缺少 Drive 文件大小，无法确认完整性。")
        if exact and spec.size != exact:
            raise ValueError(
                f"{expected} 文件大小不匹配：{spec.size} != {exact}。"
            )
        if minimum and spec.size < minimum:
            raise ValueError(f"{expected} 文件大小异常，Drive 文件可能不完整。")
        result[str(role)] = spec

    if missing:
        raise FileNotFoundError(
            "Drive 模型包缺少固定文件：" + "、".join(missing)
        )
    return result


def checkpoint_spec(payload: dict, adapter: dict) -> DriveFileSpec:
    """Compatibility helper used by older tests/callers."""
    specs = artifact_specs(payload, adapter)
    if "checkpoint" in specs:
        return specs["checkpoint"]
    if "unet" in specs:
        return specs["unet"]
    return next(iter(specs.values()))


def _bounded_int(payload: dict, key: str, default: int, minimum: int, maximum: int, step: int | None = None) -> int:
    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}.")
    if step and value % step:
        raise ValueError(f"{key} must be divisible by {step}.")
    return value


def _bounded_float(payload: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}.")
    return value


def build_prompt(model_files, payload: dict, adapter: dict, job_id: str) -> dict:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("请先填写图像提示词。")
    if len(prompt) > 12000:
        raise ValueError("图像提示词过长。")

    negative = str(payload.get("negative_prompt") or payload.get("negative") or "").strip()
    if len(negative) > 6000:
        raise ValueError("负面提示词过长。")

    defaults = adapter["defaults"]
    size_step = int(defaults.get("size_step") or 64)
    min_size = int(defaults.get("min_size") or 512)
    width = _bounded_int(payload, "width", defaults["width"], min_size, 1536, size_step)
    height = _bounded_int(payload, "height", defaults["height"], min_size, 1536, size_step)
    steps = _bounded_int(payload, "steps", defaults["steps"], 1, 80)
    cfg = _bounded_float(payload, "cfg", defaults["cfg"], 0.0, 20.0)

    seed_raw = payload.get("seed")
    if seed_raw in (None, "", -1, "-1"):
        seed = int.from_bytes(os.urandom(8), "big") & ((1 << 53) - 1)
    else:
        try:
            seed = int(seed_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("seed must be an integer.") from exc
        if seed < 0 or seed >= (1 << 63):
            raise ValueError("seed must be between 0 and 2^63-1.")

    workflow_kind = str(adapter.get("workflow_kind") or "pony_sdxl")
    if workflow_kind == "qwen_image_2_1":
        names = model_files if isinstance(model_files, dict) else {}
        unet_name = str(names.get("unet") or QWEN_UNET)
        clip_name = str(names.get("clip") or QWEN_TEXT_ENCODER)
        vae_name = str(names.get("vae") or QWEN_VAE)
        return {
            "1": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": unet_name},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": clip_name,
                    "type": "qwen_image",
                    "device": "default",
                },
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": vae_name},
            },
            "4": {
                "class_type": "TextEncodeQwenImage21",
                "inputs": {
                    "clip": ["2", 0],
                    "prompt": prompt,
                    "negative_prompt": negative,
                    "resolution": int(defaults.get("resolution") or 1024),
                },
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["4", 1],
                    "latent_image": ["5", 0],
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": defaults["sampler_name"],
                    "scheduler": defaults["scheduler"],
                    "denoise": 1.0,
                },
            },
            "7": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["6", 0], "vae": ["3", 0]},
            },
            "8": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["7", 0],
                    "filename_prefix": f"image/qwen_{job_id}",
                },
            },
        }

    if workflow_kind == "flux2_klein_4b":
        names = model_files if isinstance(model_files, dict) else {}
        unet_name = str(names.get("unet") or FLUX2_KLEIN_FP8)
        clip_name = str(names.get("clip") or FLUX2_TEXT_ENCODER)
        vae_name = str(names.get("vae") or FLUX2_VAE)
        return {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": unet_name,
                    "weight_dtype": "default",
                },
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": clip_name,
                    "type": "flux2",
                    "device": "default",
                },
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": vae_name},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["2", 0]},
            },
            "5": {
                "class_type": "ConditioningZeroOut",
                "inputs": {"conditioning": ["4", 0]},
            },
            "6": {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": seed},
            },
            "7": {
                "class_type": "KSamplerSelect",
                "inputs": {"sampler_name": defaults["sampler_name"]},
            },
            "8": {
                "class_type": "Flux2Scheduler",
                "inputs": {
                    "steps": steps,
                    "width": width,
                    "height": height,
                },
            },
            "9": {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "cfg": cfg,
                },
            },
            "10": {
                "class_type": "EmptyFlux2LatentImage",
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                },
            },
            "11": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["6", 0],
                    "guider": ["9", 0],
                    "sampler": ["7", 0],
                    "sigmas": ["8", 0],
                    "latent_image": ["10", 0],
                },
            },
            "12": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["11", 0], "vae": ["3", 0]},
            },
            "13": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["12", 0],
                    "filename_prefix": f"image/flux2_{job_id}",
                },
            },
        }

    checkpoint_name = (
        str(model_files)
        if isinstance(model_files, str)
        else str((model_files or {}).get("checkpoint") or PONY_CHECKPOINT)
    )
    clip_layer = -abs(int(defaults.get("clip_skip", 2)))

    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint_name},
        },
        "2": {
            "class_type": "CLIPSetLastLayer",
            "inputs": {
                "clip": ["1", 1],
                "stop_at_clip_layer": clip_layer,
            },
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": defaults["sampler_name"],
                "scheduler": defaults["scheduler"],
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": f"image/jvust_{job_id}",
                "images": ["7", 0],
            },
        },
    }


class ImageRuntime:
    def __init__(
        self,
        comfy_runtime,
        drive_cache: DriveCache,
        token_provider: Callable[[], str],
    ) -> None:
        self.comfy = comfy_runtime
        self.drive_cache = drive_cache
        self.token_provider = token_provider
        self.lock = threading.RLock()
        self.job_thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.logs: deque[str] = deque(maxlen=300)
        IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
        IMAGE_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.reset_state()

    def reset_state(self) -> None:
        self.job_id: str | None = None
        self.model: str | None = None
        self.adapter: str | None = None
        self.phase = "idle"
        self.detail = ""
        self.current_file: str | None = None
        self.downloaded_bytes = 0
        self.download_total_bytes: int | None = None
        self.prompt_id: str | None = None
        self.output_path: str | None = None
        self.error: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def log(self, message: str) -> None:
        value = str(message).strip()
        if value:
            with self.lock:
                self.logs.append(value)

    def _set_phase(self, phase: str, detail: str = "") -> None:
        with self.lock:
            if self.cancel.is_set():
                raise RuntimeError("任务已取消。")
            self.phase = phase
            self.detail = detail
        self.log(f"{phase}: {detail}")

    def snapshot(self) -> dict:
        with self.lock:
            total = self.download_total_bytes
            progress = (
                min(1.0, self.downloaded_bytes / total)
                if total and total > 0
                else None
            )
            running = bool(
                self.job_thread
                and self.job_thread.is_alive()
            )
            return {
                "job_id": self.job_id,
                "model": self.model,
                "adapter": self.adapter,
                "phase": self.phase,
                "detail": self.detail,
                "running": running,
                "current_file": self.current_file,
                "downloaded_bytes": self.downloaded_bytes,
                "download_total_bytes": self.download_total_bytes,
                "download_progress": progress,
                "prompt_id": self.prompt_id,
                "output_ready": bool(
                    self.phase == "complete" and self.output_path
                    and Path(self.output_path).is_file()
                    and Path(self.output_path).stat().st_size > 0
                ),
                "output_name": (
                    Path(self.output_path).name if self.output_path else None
                ),
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "logs": list(self.logs)[-30:],
                "hardware": managed_comfy_hardware(),
                "supported_adapters": [
                    {"id": key, "label": value["label"]}
                    for key, value in ADAPTERS.items()
                ],
            }

    def start(self, payload: dict) -> dict:
        name = str(payload.get("name") or payload.get("model_name") or "")
        model_id = str(payload.get("model_id") or "")
        package_path = str(payload.get("package_path") or "")
        matched = adapter_for(name, model_id, package_path)
        if not matched:
            raise ValueError("这个图像模型还没有网页自动运行适配器。")
        adapter_key, adapter = matched

        hardware = managed_comfy_hardware(adapter)
        if not hardware["supported"]:
            raise RuntimeError("硬件不支持：" + str(hardware["detail"]))

        if self.comfy.snapshot().get("running"):
            raise RuntimeError("已有视频任务正在使用 ComfyUI，请先等待或停止视频任务。")

        self.token_provider()
        checkpoint_spec(payload, adapter)

        with self.lock:
            if self.job_thread and self.job_thread.is_alive():
                raise RuntimeError("已有图像任务正在运行。")
            self.comfy.cancel.clear()
            self.cancel.clear()
            self.reset_state()
            self.job_id = uuid.uuid4().hex
            self.model = name or adapter["label"]
            self.adapter = adapter_key
            self.phase = "starting"
            self.detail = "正在准备图像运行环境"
            self.started_at = time.time()
            job_id = self.job_id

            thread = threading.Thread(
                target=self._run_job,
                args=(job_id, adapter_key, dict(payload)),
                daemon=True,
            )
            self.job_thread = thread
            thread.start()
        return self.snapshot()

    def stop(self) -> dict:
        with self.lock:
            thread = self.job_thread
            if not thread or not thread.is_alive():
                return self.snapshot()
            self.cancel.set()
            # Image preparation borrows this helper, but idle stop must never
            # cancel a video or another ComfyUI user's prompt.
            self.comfy.cancel.set()
            prompt_id = self.prompt_id
            self.phase = "cancelling"
            self.detail = "正在停止图像任务，等待工作线程退出"
        if prompt_id:
            self._cancel_prompt(prompt_id)
        if thread is not threading.current_thread():
            thread.join(timeout=1)
        with self.lock:
            if self.phase == "cancelling" and not thread.is_alive():
                self.phase = "cancelled"
                self.detail = "图像任务已取消"
                self.finished_at = time.time()
        return self.snapshot()

    def _cancel_prompt(self, prompt_id: str) -> None:
        # The job endpoint checks ownership atomically; never use a global
        # interrupt when another client may be using the same ComfyUI server.
        for endpoint, payload in (("/queue", {"delete": [prompt_id]}), ("/api/jobs/" + quote(prompt_id, safe="") + "/cancel", {})):
            try:
                json_request(COMFY_BASE + endpoint, method="POST", payload=payload, timeout=2)
            except Exception as error:
                self.log("Could not cancel owned ComfyUI prompt; verify ComfyUI v0.37.0 or newer: " + repr(error))

    def shutdown(self) -> None:
        self.cancel.set()

    def output_file(self, job_id: str) -> Path:
        with self.lock:
            if self.phase != "complete" or not self.job_id or job_id != self.job_id or not self.output_path:
                raise FileNotFoundError("图像输出不存在。")
            path = Path(self.output_path).resolve()
        root = IMAGE_OUTPUT_ROOT.resolve()
        if os.path.commonpath([str(root), str(path)]) != str(root):
            raise ValueError("非法图像输出路径。")
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError("图像输出文件不存在。")
        return path

    def _progress(self, downloaded: int, total: int | None) -> None:
        if self.cancel.is_set():
            raise RuntimeError("任务已取消。")
        with self.lock:
            self.downloaded_bytes = int(downloaded)
            self.download_total_bytes = int(total) if total else None

    def _install_artifact(
        self,
        comfy_root: Path,
        spec: DriveFileSpec,
        token: str,
        directories,
    ) -> str:
        self._set_phase("downloading_model", f"准备 Drive 模型文件：{spec.name}")
        with self.lock:
            self.current_file = spec.name
        cached = self.drive_cache.download(spec, token, progress=self._progress)

        for directory in tuple(directories or ()):
            destination = (
                comfy_root / "ComfyUI" / "models" / str(directory) / spec.name
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.stat().st_size == cached.stat().st_size:
                    continue
                destination.unlink()
            try:
                os.link(cached, destination)
                self.log(f"Linked model into ComfyUI: {directory}/{destination.name}")
            except OSError:
                shutil.copy2(cached, destination)
                self.log(f"Copied model into ComfyUI: {directory}/{destination.name}")
        return spec.name

    def _ensure_comfy_gguf(self, comfy_root: Path, python: Path) -> bool:
        custom_root = comfy_root / "ComfyUI" / "custom_nodes"
        target = custom_root / "ComfyUI-GGUF"
        marker = target / ".jvust_requirements_ok"
        installed_new = False

        if not (target / "__init__.py").exists():
            self._set_phase(
                "preparing_comfy_gguf",
                "首次使用 Qwen-Image：安装 ComfyUI-GGUF 节点",
            )
            custom_root.mkdir(parents=True, exist_ok=True)
            download_root = IMAGE_ROOT / "downloads"
            download_root.mkdir(parents=True, exist_ok=True)
            archive = download_root / COMFY_GGUF_ARCHIVE_NAME
            self.comfy._download(
                COMFY_GGUF_ARCHIVE_URL,
                archive,
                COMFY_GGUF_ARCHIVE_NAME,
            )

            extract_root = download_root / ("gguf-node-" + uuid.uuid4().hex)
            extract_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(extract_root)
            candidates = [
                path
                for path in extract_root.iterdir()
                if path.is_dir() and path.name.lower().startswith("comfyui-gguf")
            ]
            if not candidates:
                shutil.rmtree(extract_root, ignore_errors=True)
                raise RuntimeError("ComfyUI-GGUF 下载完成，但压缩包结构无法识别。")
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(candidates[0]), str(target))
            shutil.rmtree(extract_root, ignore_errors=True)
            archive.unlink(missing_ok=True)
            installed_new = True

        if not marker.exists():
            requirements = target / "requirements.txt"
            command = [
                str(python),
                "-s",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
            ]
            if requirements.exists():
                command.extend(["-r", str(requirements)])
            else:
                command.extend(["gguf>=0.13.0", "sentencepiece", "protobuf"])
            result = subprocess.run(
                command,
                cwd=str(comfy_root),
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "ComfyUI-GGUF 依赖安装失败："
                    + (result.stderr or result.stdout or "unknown pip error")[-3000:]
                )
            marker.write_text("ok\n", encoding="utf-8")
            installed_new = True

        return installed_new

    def _verify_required_nodes(self, adapter: dict) -> None:
        required = tuple(adapter.get("required_nodes") or ())
        required_values = adapter.get("required_node_values") or {}
        if not required and not required_values:
            return
        info = json_request(COMFY_BASE + "/object_info", timeout=60)
        missing = [name for name in required if name not in info]
        label = str(adapter.get("label") or "图像模型")
        if missing:
            raise RuntimeError(
                f"ComfyUI 缺少 {label} 所需节点："
                + "、".join(missing)
                + "。请更新 Runtime/ComfyUI 后重试。"
            )

        value_errors = []
        for node_name, fields in required_values.items():
            node = info.get(node_name) or {}
            input_info = node.get("input") or {}
            required_info = input_info.get("required") or {}
            optional_info = input_info.get("optional") or {}
            for field_name, expected in (fields or {}).items():
                spec = required_info.get(field_name)
                if spec is None:
                    spec = optional_info.get(field_name)
                choices = None
                if isinstance(spec, (list, tuple)) and spec:
                    candidate = spec[0]
                    if isinstance(candidate, (list, tuple)):
                        choices = list(candidate)
                if choices is None:
                    value_errors.append(
                        f"{node_name}.{field_name} 无法读取可选值"
                    )
                elif expected not in choices:
                    value_errors.append(
                        f"{node_name}.{field_name} 不支持 {expected}"
                    )

        if value_errors:
            raise RuntimeError(
                f"ComfyUI 与 {label} 工作流不兼容："
                + "；".join(value_errors)
                + "。请更新 Runtime/ComfyUI 后重试。"
            )

    def _history(self, prompt_id: str) -> dict | None:
        try:
            data = json_request(COMFY_BASE + "/history/" + prompt_id, timeout=10)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return data.get(prompt_id) or None

    def _resolve_image(self, comfy_root: Path, entry: dict, started: float) -> Path:
        outputs = entry.get("outputs") or {}
        output_node = "13" if self.adapter == "flux2_klein_4b_fp8" else "8"
        output_dir = (comfy_root / "ComfyUI" / "output").resolve()
        for node in [outputs.get(output_node)]:
            if not isinstance(node, dict):
                continue
            for image in node.get("images") or []:
                if not isinstance(image, dict) or not image.get("filename"):
                    continue
                filename = str(image["filename"])
                subfolder = str(image.get("subfolder") or "")
                folder_type = str(image.get("type") or "output")
                candidate = (output_dir / subfolder / filename).resolve()
                if folder_type != "output" or not candidate.is_relative_to(output_dir):
                    raise ValueError("ComfyUI 输出路径超出图像输出目录。")
                if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("ComfyUI 输出不是支持的图像文件。")
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
        raise RuntimeError("ComfyUI 已完成，但当前任务的输出节点没有可用的图像文件。")

    def _wait_for_result(self, comfy_root: Path, prompt_id: str, started: float) -> Path:
        deadline = time.time() + IMAGE_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self.cancel.is_set():
                raise RuntimeError("任务已取消。")
            entry = self._history(prompt_id)
            if entry:
                status = entry.get("status") or {}
                status_str = str(status.get("status_str") or "").lower()
                if status_str == "error":
                    messages = status.get("messages") or []
                    raise RuntimeError(
                        "ComfyUI 图像生成失败："
                        + json.dumps(messages[-5:], ensure_ascii=False)
                    )
                if entry.get("outputs") and (status_str == "success" or status.get("completed") is True):
                    return self._resolve_image(comfy_root, entry, started)
            time.sleep(1.5)
        raise RuntimeError("图像生成超时。")

    def _run_job(self, job_id: str, adapter_key: str, payload: dict) -> None:
        try:
            adapter = ADAPTERS[adapter_key]
            specs = artifact_specs(payload, adapter)
            token = self.token_provider()

            self._set_phase("preparing_comfyui", "正在准备 managed ComfyUI")
            portable, python, _ = self.comfy._ensure_comfyui()

            installed_names = {}
            for role, spec in specs.items():
                requirement = (adapter.get("artifacts") or {}).get(role) or {}
                installed_names[role] = self._install_artifact(
                    portable,
                    spec,
                    token,
                    requirement.get("directories") or (),
                )

            if adapter.get("requires_comfy_gguf"):
                installed_new = self._ensure_comfy_gguf(portable, python)
                if installed_new:
                    process = self.comfy.process
                    if process and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        self.comfy.process = None

            self.comfy._ensure_comfyui_server()
            self._verify_required_nodes(adapter)

            workflow_kind = adapter.get("workflow_kind")
            label = {
                "qwen_image_2_1": "正在构建 Qwen-Image 2.1 GGUF 工作流",
                "flux2_klein_4b": "正在构建 FLUX.2 Klein 4B distilled 工作流",
            }.get(workflow_kind, "正在构建 Pony SDXL 图像工作流")
            self._set_phase("building_workflow", label)
            prompt = build_prompt(installed_names, payload, adapter, job_id)

            self._set_phase("queued", "正在提交图像任务")
            prompt_id = self.comfy._queue_prompt(prompt)
            with self.lock:
                self.prompt_id = prompt_id

            self._set_phase("generating", "ComfyUI 正在生成图像")
            generation_started = time.time()
            source = self._wait_for_result(portable, prompt_id, generation_started)
            if self.cancel.is_set():
                raise RuntimeError("任务已取消。")

            suffix = source.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                suffix = ".png"
            output = IMAGE_OUTPUT_ROOT / f"{job_id}{suffix}"
            shutil.copy2(source, output)

            with self.lock:
                if self.cancel.is_set():
                    output.unlink(missing_ok=True)
                    raise RuntimeError("任务已取消。")
                self.output_path = str(output)
                self.phase = "complete"
                self.detail = "图像生成完成"
                self.error = None
                self.finished_at = time.time()
                self.current_file = None
            self.log(f"Image complete: {output.name}")

        except Exception as error:
            if self.prompt_id:
                self._cancel_prompt(self.prompt_id)
            with self.lock:
                if self.cancel.is_set():
                    self.phase = "cancelled"
                    self.detail = "图像任务已取消"
                else:
                    self.phase = "failed"
                    self.detail = "图像任务失败"
                self.error = str(error)
                self.finished_at = time.time()
            self.log("Image error: " + repr(error))
