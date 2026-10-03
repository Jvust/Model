from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from .drive_cache import default_cache_root
    from .hardware import managed_comfy_preflight, nvidia_status
except ImportError:
    from drive_cache import default_cache_root
    from hardware import managed_comfy_preflight, nvidia_status

try:
    import py7zr  # bundled in the standalone Windows Runtime build
except Exception:  # pragma: no cover - source-tree tests do not require it
    py7zr = None


COMFY_HOST = "127.0.0.1"
COMFY_PORT = int(os.environ.get("MODEL_COMFYUI_PORT", "8189"))
COMFY_BASE = f"http://{COMFY_HOST}:{COMFY_PORT}"

COMFY_RELEASE_TAG = "v0.37.0"
COMFY_ARCHIVE_NAME = "ComfyUI_windows_portable_nvidia_cu126.7z"
COMFY_ARCHIVE_URL = (
    "https://github.com/Comfy-Org/ComfyUI/releases/download/"
    f"{COMFY_RELEASE_TAG}/{COMFY_ARCHIVE_NAME}"
)

DOWNLOAD_CHUNK = 8 * 1024 * 1024
VIDEO_TIMEOUT_SECONDS = int(os.environ.get("MODEL_VIDEO_TIMEOUT_SECONDS", "21600"))


def app_data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return (Path(local) / "JvustModel").resolve()
    return (Path.home() / ".jvust-model").resolve()


VIDEO_ROOT = Path(
    os.environ.get("MODEL_VIDEO_ROOT", str(default_cache_root() / "video"))
).expanduser().resolve()
COMFY_MANAGED_ROOT = VIDEO_ROOT / "comfyui"
VIDEO_OUTPUT_ROOT = VIDEO_ROOT / "outputs"
DOWNLOAD_ROOT = VIDEO_ROOT / "downloads"


def resource_path(relative: str) -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidate = Path(frozen) / relative
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / relative


ADAPTERS = {
    "wan2.2-ti2v-5b": {
        "label": "Wan2.2 TI2V 5B",
        "match": ("wan2.2-ti2v-5b", "wan2.2 ti2v 5b"),
        "workflow": "workflows/video_wan2_2_5B_ti2v.json",
        "output_node": "58",
        "min_vram_mb": 12 * 1024,
        "min_disk_free_gb": 10.0,
        "defaults": {
            "width": 832,
            "height": 480,
            "frames": 49,
            "fps": 24,
            "steps": 20,
            "cfg": 5.0,
        },
    },
    "hunyuanvideo-1.5": {
        "label": "HunyuanVideo 1.5",
        "match": ("hunyuanvideo-1.5", "hunyuanvideo 1.5"),
        "workflow": "workflows/video_hunyuan_video_1.5_720p_t2v.json",
        "output_node": "102",
        "min_vram_mb": 16 * 1024,
        "min_disk_free_gb": 10.0,
        "defaults": {
            "width": 1280,
            "height": 720,
            "frames": 49,
            "fps": 24,
            "steps": 20,
            "cfg": 6.0,
        },
    },
}


def adapter_for(name: str, model_id: str = "") -> tuple[str, dict] | None:
    hay = f"{model_id} {name}".strip().lower()
    for key, adapter in ADAPTERS.items():
        if any(token in hay for token in adapter["match"]):
            return key, adapter
    return None


def json_request(url: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 10.0):
    data = None
    headers = {"User-Agent": "JvustModel/0.9"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def comfy_reachable() -> bool:
    try:
        data = json_request(COMFY_BASE + "/system_stats", timeout=1.0)
        return isinstance(data, dict)
    except Exception:
        return False


def nvidia_available() -> bool:
    return bool(nvidia_status().get("detected"))


def adapter_hardware(adapter: dict) -> dict:
    return managed_comfy_preflight(
        VIDEO_ROOT,
        min_vram_mb=int(adapter.get("min_vram_mb") or 0),
        min_disk_free_gb=float(adapter.get("min_disk_free_gb") or 10.0),
    )


def _link_map(workflow: dict) -> dict[int, tuple]:
    result = {}
    for link in workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 6:
            result[int(link[0])] = tuple(link)
    return result


def disconnect_input(workflow: dict, node_id: int, input_name: str) -> None:
    removed: set[int] = set()
    for node in workflow.get("nodes") or []:
        if int(node.get("id")) != int(node_id):
            continue
        for item in node.get("inputs") or []:
            if item.get("name") == input_name and item.get("link") is not None:
                removed.add(int(item["link"]))
                item["link"] = None

    if removed:
        workflow["links"] = [
            link
            for link in workflow.get("links") or []
            if int(link[0]) not in removed
        ]


def ancestor_nodes(workflow: dict, output_node_ids: set[str]) -> set[str]:
    links = _link_map(workflow)
    nodes = {str(node.get("id")): node for node in workflow.get("nodes") or []}
    keep: set[str] = set()
    stack = list(output_node_ids)

    while stack:
        node_id = str(stack.pop())
        if node_id in keep or node_id not in nodes:
            continue
        keep.add(node_id)
        node = nodes[node_id]
        for item in node.get("inputs") or []:
            link_id = item.get("link")
            if link_id is None:
                continue
            link = links.get(int(link_id))
            if link:
                stack.append(str(link[1]))
    return keep


def model_requirements(workflow: dict, keep: set[str]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    items: list[dict] = []
    for node in workflow.get("nodes") or []:
        if str(node.get("id")) not in keep:
            continue
        for model in (node.get("properties") or {}).get("models") or []:
            name = str(model.get("name") or "").strip()
            url = str(model.get("url") or "").strip()
            directory = str(model.get("directory") or "").strip()
            if not name or not url or not directory:
                continue
            key = (directory, name)
            if key in seen:
                continue
            seen.add(key)
            items.append({"name": name, "url": url, "directory": directory})
    return items


def _widget_values(node: dict, expected: int) -> list:
    values = list(node.get("widgets_values") or [])
    # ComfyUI frontend injects a "control after generate" widget next to seed.
    if (
        len(values) == expected + 1
        and len(values) >= 2
        and isinstance(values[1], str)
        and values[1] in {"fixed", "increment", "decrement", "randomize"}
    ):
        values.pop(1)
    return values


def workflow_to_api_prompt(workflow: dict, object_info: dict, keep: set[str]) -> dict:
    links = _link_map(workflow)
    nodes = {str(n.get("id")): n for n in workflow.get("nodes") or []}
    prompt: dict[str, dict] = {}

    for node_id in keep:
        node = nodes.get(node_id)
        if not node or int(node.get("mode", 0) or 0) == 4:
            continue
        class_type = str(node.get("type") or "")
        info = object_info.get(class_type)
        if not info:
            # Notes and frontend-only nodes are intentionally omitted.
            continue

        inputs: dict = {}
        socket_names: set[str] = set()
        for item in node.get("inputs") or []:
            name = str(item.get("name") or "")
            socket_names.add(name)
            link_id = item.get("link")
            if link_id is None:
                continue
            link = links.get(int(link_id))
            if not link:
                continue
            origin_id, origin_slot = str(link[1]), int(link[2])
            if origin_id in keep:
                inputs[name] = [origin_id, origin_slot]

        schema = info.get("input") or {}
        ordered_names: list[str] = []
        for section in ("required", "optional"):
            values = schema.get(section) or {}
            if isinstance(values, dict):
                ordered_names.extend(values.keys())

        widget_names = [name for name in ordered_names if name not in socket_names]
        values = _widget_values(node, len(widget_names))
        if len(values) < len(widget_names):
            raise ValueError(
                f"Workflow node {node_id} ({class_type}) has "
                f"{len(values)} widget values but ComfyUI expects {len(widget_names)}."
            )

        for name, value in zip(widget_names, values):
            inputs[name] = value

        prompt[node_id] = {"class_type": class_type, "inputs": inputs}

    return prompt


def _set_widget(node: dict, index: int, value) -> None:
    values = list(node.get("widgets_values") or [])
    while len(values) <= index:
        values.append(None)
    values[index] = value
    node["widgets_values"] = values


def validate_video_payload(adapter_key: str, payload: dict) -> dict:
    """Validate before any backend/model download, preserving explicit zero CFG."""
    defaults = ADAPTERS[adapter_key]["defaults"]
    result = dict(payload)
    for field, maximum in (("prompt", 12000), ("negative_prompt", 6000)):
        value = payload.get(field, "")
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{field} 必须是长度不超过 {maximum} 的文本。")
        result[field] = value.strip()
    if not result["prompt"]:
        raise ValueError("视频提示词不能为空。")
    bounds = {"width": (256, 1920), "height": (256, 1080), "frames": (9, 241),
              "fps": (1, 60), "steps": (1, 100), "seed": (0, 2**53 - 1)}
    for field, (minimum, maximum) in bounds.items():
        value = payload.get(field)
        if value is None:
            value = int(time.time() * 1000) % (2**53) if field == "seed" else defaults[field]
        try:
            number = int(value)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError(f"{field} 必须是整数。") from error
        if isinstance(value, bool) or str(number) != str(value).strip() or not minimum <= number <= maximum:
            raise ValueError(f"{field} 必须是 {minimum}–{maximum} 的整数。")
        result[field] = number
    if result["width"] % 16 or result["height"] % 16:
        raise ValueError("视频宽高必须是 16 的倍数。")
    if (result["frames"] - 1) % 4:
        raise ValueError("视频帧数必须是 4n+1，范围 9–241。")
    if adapter_key == "hunyuanvideo-1.5" and (result["width"], result["height"]) != (1280, 720):
        raise ValueError("当前 HunyuanVideo 1.5 适配使用固定 1280×720 工作流。")
    value = payload.get("cfg", defaults["cfg"])
    try:
        cfg = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("cfg 必须在 0–20。") from error
    if isinstance(value, bool) or not math.isfinite(cfg) or not 0 <= cfg <= 20:
        raise ValueError("cfg 必须在 0–20。")
    result["cfg"] = cfg
    return result


def customize_workflow(workflow: dict, adapter_key: str, payload: dict, job_id: str) -> tuple[dict, set[str]]:
    workflow = copy.deepcopy(workflow)
    adapter = ADAPTERS[adapter_key]
    payload = validate_video_payload(adapter_key, payload)
    prompt_text, negative = payload["prompt"], payload["negative_prompt"]
    width, height, frames = payload["width"], payload["height"], payload["frames"]
    fps, steps, cfg, seed = payload["fps"], payload["steps"], payload["cfg"], payload["seed"]

    if adapter_key == "wan2.2-ti2v-5b":
        # Template includes an optional example start image. Remove it for text-to-video.
        disconnect_input(workflow, 55, "start_image")

    for node in workflow.get("nodes") or []:
        node_type = str(node.get("type") or "")
        title = str(node.get("title") or "")
        if node_type == "CLIPTextEncode":
            if "Positive" in title:
                _set_widget(node, 0, prompt_text)
            elif "Negative" in title:
                _set_widget(node, 0, negative)

        if adapter_key == "wan2.2-ti2v-5b":
            if node_type == "Wan22ImageToVideoLatent":
                node["widgets_values"] = [width, height, frames, 1]
            elif node_type == "KSampler":
                values = list(node.get("widgets_values") or [])
                while len(values) < 7:
                    values.append(None)
                values[0] = seed
                values[1] = "fixed"
                values[2] = steps
                values[3] = cfg
                node["widgets_values"] = values
            elif node_type == "CreateVideo":
                _set_widget(node, 0, fps)
            elif node_type == "SaveVideo":
                values = list(node.get("widgets_values") or [])
                if values:
                    values[0] = f"video/jvust_{job_id}"
                    node["widgets_values"] = values

        elif adapter_key == "hunyuanvideo-1.5":
            if node_type == "EmptyHunyuanVideo15Latent":
                node["widgets_values"] = [width, height, frames, 1]
            elif node_type == "RandomNoise":
                node["widgets_values"] = [seed, "fixed"]
            elif node_type == "BasicScheduler":
                values = list(node.get("widgets_values") or [])
                while len(values) < 3:
                    values.append(None)
                values[1] = steps
                node["widgets_values"] = values
            elif node_type == "CFGGuider":
                _set_widget(node, 0, cfg)
            elif node_type == "CreateVideo":
                _set_widget(node, 0, fps)
            elif node_type == "SaveVideo":
                values = list(node.get("widgets_values") or [])
                if values:
                    values[0] = f"video/jvust_{job_id}"
                    node["widgets_values"] = values

    keep = ancestor_nodes(workflow, {str(adapter["output_node"])})
    return workflow, keep


def _content_total(response, offset: int) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1]
        if tail.isdigit():
            return int(tail)
    length = response.headers.get("Content-Length")
    if length and str(length).isdigit():
        return offset + int(length)
    return None


class VideoRuntime:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.job_thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.logs: deque[str] = deque(maxlen=300)
        self.reset_state()
        VIDEO_ROOT.mkdir(parents=True, exist_ok=True)
        VIDEO_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

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
        self.parameters: dict = {}

    def log(self, message: str) -> None:
        message = str(message).strip()
        if message:
            with self.lock:
                self.logs.append(message)

    def snapshot(self) -> dict:
        with self.lock:
            total = self.download_total_bytes
            progress = (
                min(1.0, self.downloaded_bytes / total)
                if self.phase in {"preparing_comfyui", "downloading_models"} and total and total > 0
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
                "output_ready": bool(self.phase == "complete" and self.output_path and Path(self.output_path).is_file() and Path(self.output_path).stat().st_size > 0),
                "output_name": Path(self.output_path).name if self.output_path else None,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "elapsed_seconds": round(max(0, (self.finished_at or time.time()) - self.started_at), 1) if self.started_at else 0,
                "parameters": dict(self.parameters),
                "duration_seconds": round(self.parameters["frames"] / self.parameters["fps"], 2) if self.parameters else None,
                "logs": list(self.logs)[-30:],
                "supported_adapters": [
                    {"id": key, "label": value["label"]}
                    for key, value in ADAPTERS.items()
                ],
                "workflow_resources_ready": all(
                    resource_path(adapter["workflow"]).exists()
                    for adapter in ADAPTERS.values()
                ),
                "archive_support": py7zr is not None,
            }

    def start(self, payload: dict) -> dict:
        name = str(payload.get("name") or payload.get("model_name") or "")
        model_id = str(payload.get("model_id") or "")
        matched = adapter_for(name, model_id)
        if not matched:
            raise ValueError("这个视频模型还没有自动运行适配器。")
        adapter_key, adapter = matched
        payload = validate_video_payload(adapter_key, payload)

        hardware = adapter_hardware(adapter)
        if not hardware["supported"]:
            raise RuntimeError("硬件不支持：" + str(hardware["detail"]))

        with self.lock:
            if self.job_thread and self.job_thread.is_alive():
                raise RuntimeError("已有视频任务正在运行。")
            self.cancel.clear()
            self.reset_state()
            self.job_id = uuid.uuid4().hex
            self.model = name or adapter["label"]
            self.adapter = adapter_key
            self.phase = "starting"
            self.detail = "正在准备视频运行环境"
            self.started_at = time.time()
            self.parameters = {name: payload[name] for name in ("width", "height", "frames", "fps", "steps", "cfg", "seed")}
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
                if self.phase == "cancelling":
                    self.phase = "cancelled"
                    self.detail = "任务已取消"
                    self.finished_at = time.time()
                return self.snapshot()
            self.cancel.set()
            prompt_id = self.prompt_id
            if self.phase not in {"idle", "complete", "failed"}:
                self.phase = "cancelling"
                self.detail = "正在停止视频任务，等待工作线程退出"
        if prompt_id:
            self._cancel_prompt(prompt_id)
        if thread is not threading.current_thread():
            thread.join(timeout=1)
        with self.lock:
            if self.phase == "cancelling" and not (self.job_thread and self.job_thread.is_alive()):
                self.phase = "cancelled"
                self.detail = "任务已取消"
                self.finished_at = time.time()
        return self.snapshot()

    def _cancel_prompt(self, prompt_id: str) -> None:
        # v0.37.0 performs an atomic interrupt_if_running here. Do not fall
        # back to /interrupt: old servers may ignore its prompt_id parameter.
        for endpoint, payload in (("/queue", {"delete": [prompt_id]}), ("/api/jobs/" + quote(prompt_id, safe="") + "/cancel", {})):
            try:
                json_request(COMFY_BASE + endpoint, method="POST", payload=payload, timeout=2)
            except Exception as error:
                self.log("Could not cancel owned ComfyUI prompt; verify ComfyUI v0.37.0 or newer: " + repr(error))

    def shutdown(self) -> None:
        self.cancel.set()
        process = self.process
        self.process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()

    def output_file(self, job_id: str) -> Path:
        with self.lock:
            if self.phase != "complete" or not self.job_id or job_id != self.job_id or not self.output_path:
                raise FileNotFoundError("视频输出不存在。")
            path = Path(self.output_path).resolve()
        root = VIDEO_OUTPUT_ROOT.resolve()
        if os.path.commonpath([str(root), str(path)]) != str(root):
            raise ValueError("非法视频输出路径。")
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError("视频输出文件不存在。")
        return path

    def _set_phase(self, phase: str, detail: str = "") -> None:
        with self.lock:
            if self.cancel.is_set():
                raise RuntimeError("任务已取消。")
            self.phase = phase
            self.detail = detail
        self.log(f"{phase}: {detail}")

    def _download(self, url: str, destination: Path, label: str) -> Path:
        if destination.exists() and destination.stat().st_size > 0:
            self.log(f"Using cached file: {destination.name}")
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        offset = partial.stat().st_size if partial.exists() else 0

        headers = {"User-Agent": "JvustModel/0.9"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, headers=headers, method="GET")

        try:
            response = urlopen(request, timeout=60)
        except HTTPError as exc:
            raise RuntimeError(f"下载失败 HTTP {exc.code}: {label}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"无法下载 {label}: {exc}") from exc

        with response:
            status = int(getattr(response, "status", 200))
            if status == 206:
                # A valid total alone cannot prove that these bytes follow our partial.
                content_range = str(response.headers.get("Content-Range") or "")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range.strip())
                if not match:
                    raise RuntimeError(f"下载断点响应无效：{label} 缺少有效 Content-Range。")
                start, end, total_range = map(int, match.groups())
                length = response.headers.get("Content-Length")
                if start != offset or end < start or end >= total_range or (
                    length is not None and str(length) != str(end - start + 1)
                ):
                    raise RuntimeError(f"下载断点响应不匹配：{label}，原断点未改动。")
            elif status != 200:
                raise RuntimeError(f"下载响应状态无效 HTTP {status}: {label}")
            if offset and status != 206:
                partial.unlink(missing_ok=True)
                offset = 0

            mode = "ab" if offset and status == 206 else "wb"
            downloaded = offset if mode == "ab" else 0
            total = _content_total(response, downloaded)
            if total and total > downloaded:
                remaining = total - downloaded
                free = shutil.disk_usage(destination.parent).free
                reserve = 2 * 1024 * 1024 * 1024
                if free < remaining + reserve:
                    raise RuntimeError(
                        "本机缓存空间不足："
                        f"{label} 还需要约 {remaining / (1024**3):.1f} GB，"
                        f"当前可用约 {free / (1024**3):.1f} GB。"
                    )

            with self.lock:
                self.current_file = label
                self.downloaded_bytes = downloaded
                self.download_total_bytes = total

            with partial.open(mode) as handle:
                while True:
                    if self.cancel.is_set():
                        raise RuntimeError("任务已取消。")
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    with self.lock:
                        self.downloaded_bytes = downloaded
                        self.download_total_bytes = total

        if total is not None and downloaded != total:
            raise RuntimeError(f"下载未完成：{label} ({downloaded}/{total} 字节)，已保留断点。")
        if downloaded == 0:
            raise RuntimeError(f"下载返回空文件：{label}。")
        partial.replace(destination)
        with self.lock:
            self.downloaded_bytes = destination.stat().st_size
            self.download_total_bytes = destination.stat().st_size
        return destination

    def _locate_portable(self) -> tuple[Path, Path, Path] | None:
        if not COMFY_MANAGED_ROOT.exists():
            return None
        for python in COMFY_MANAGED_ROOT.rglob("python.exe"):
            if python.parent.name != "python_embeded":
                continue
            portable = python.parent.parent
            main = portable / "ComfyUI" / "main.py"
            if main.exists():
                return portable, python, main
        return None

    def _ensure_comfyui(self) -> tuple[Path, Path, Path]:
        located = self._locate_portable()
        if located:
            return located

        if os.name == "nt" and not nvidia_available():
            raise RuntimeError(
                "需要远程 NVIDIA Runtime：本机未检测到 NVIDIA GPU / nvidia-smi，"
                "因此没有开始下载 managed ComfyUI。"
            )

        if py7zr is None:
            raise RuntimeError("Standalone Runtime 缺少 7z 解压组件，请更新 Runtime。")

        self._set_phase("preparing_comfyui", "首次使用：下载 ComfyUI Windows Portable")
        archive = DOWNLOAD_ROOT / COMFY_ARCHIVE_NAME
        self._download(COMFY_ARCHIVE_URL, archive, COMFY_ARCHIVE_NAME)

        self._set_phase("extracting_comfyui", "正在解压 ComfyUI")
        COMFY_MANAGED_ROOT.mkdir(parents=True, exist_ok=True)
        with py7zr.SevenZipFile(archive, mode="r") as zf:
            zf.extractall(path=COMFY_MANAGED_ROOT)
        archive.unlink(missing_ok=True)

        located = self._locate_portable()
        if not located:
            raise RuntimeError("ComfyUI 解压完成，但没有找到 portable Python/main.py。")
        return located

    def _read_process_output(self, process: subprocess.Popen[str]) -> None:
        if not process.stdout:
            return
        for line in process.stdout:
            self.log("[ComfyUI] " + line.rstrip())

    def _ensure_comfyui_server(self) -> tuple[Path, Path, Path]:
        portable, python, main = self._ensure_comfyui()
        if comfy_reachable():
            return portable, python, main

        process = self.process
        if process and process.poll() is None:
            # Give an already-starting instance a chance to become reachable.
            pass
        else:
            self._set_phase("starting_comfyui", "正在启动 ComfyUI 后端")
            creationflags = 0
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW
            command = [
                str(python),
                "-s",
                str(main),
                "--listen",
                COMFY_HOST,
                "--port",
                str(COMFY_PORT),
                "--disable-auto-launch",
                "--windows-standalone-build",
            ]
            process = subprocess.Popen(
                command,
                cwd=str(portable),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            self.process = process
            threading.Thread(
                target=self._read_process_output, args=(process,), daemon=True
            ).start()

        deadline = time.time() + 240
        while time.time() < deadline:
            if self.cancel.is_set():
                raise RuntimeError("任务已取消。")
            if process and process.poll() is not None:
                raise RuntimeError(
                    f"ComfyUI 启动失败，退出代码 {process.returncode}。"
                )
            if comfy_reachable():
                return portable, python, main
            time.sleep(1)

        raise RuntimeError("等待 ComfyUI 启动超时。")

    def _ensure_models(self, comfy_root: Path, workflow: dict, keep: set[str]) -> None:
        requirements = model_requirements(workflow, keep)
        if not requirements:
            raise RuntimeError("工作流没有声明需要的模型文件。")

        total_count = len(requirements)
        for index, item in enumerate(requirements, start=1):
            if self.cancel.is_set():
                raise RuntimeError("任务已取消。")
            self._set_phase(
                "downloading_models",
                f"准备模型文件 {index}/{total_count}: {item['name']}",
            )
            destination = (
                comfy_root
                / "ComfyUI"
                / "models"
                / item["directory"]
                / item["name"]
            )
            self._download(item["url"], destination, item["name"])

    def _queue_prompt(self, prompt: dict) -> str:
        response = json_request(
            COMFY_BASE + "/prompt",
            method="POST",
            payload={"prompt": prompt},
            timeout=30,
        )
        prompt_id = str(response.get("prompt_id") or "")
        errors = response.get("node_errors") or {}
        if not prompt_id:
            raise RuntimeError("ComfyUI 拒绝了工作流：" + json.dumps(errors, ensure_ascii=False))
        return prompt_id

    def _history(self, prompt_id: str) -> dict | None:
        try:
            data = json_request(
                COMFY_BASE + "/history/" + prompt_id,
                timeout=10,
            )
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return data.get(prompt_id) or None

    def _find_output_candidate(self, value) -> dict | None:
        if isinstance(value, dict):
            if value.get("filename"):
                return value
            for child in value.values():
                found = self._find_output_candidate(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = self._find_output_candidate(child)
                if found:
                    return found
        return None

    def _resolve_history_output(self, comfy_root: Path, entry: dict, started: float) -> Path:
        outputs = entry.get("outputs") or {}
        output_node = ADAPTERS[self.adapter]["output_node"] if self.adapter in ADAPTERS else None
        candidate = self._find_output_candidate(outputs.get(output_node, {}) if output_node else outputs)
        output_dir = (comfy_root / "ComfyUI" / "output").resolve()
        if candidate:
            filename = str(candidate.get("filename") or "")
            subfolder = str(candidate.get("subfolder") or "")
            folder_type = str(candidate.get("type") or "output")
            path = (output_dir / subfolder / filename).resolve()
            if folder_type != "output" or not path.is_relative_to(output_dir):
                raise ValueError("ComfyUI 输出路径超出视频输出目录。")
            if path.suffix.lower() not in {".mp4", ".webm", ".mkv", ".gif"}:
                raise ValueError("ComfyUI 输出不是支持的视频文件。")
            if path.is_file() and path.stat().st_size > 0:
                return path
        raise RuntimeError("ComfyUI 已完成，但当前任务的输出节点没有可用的视频文件。")

    def _wait_for_result(self, comfy_root: Path, prompt_id: str, started: float) -> Path:
        deadline = time.time() + VIDEO_TIMEOUT_SECONDS
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
                        "ComfyUI 生成失败：" + json.dumps(messages[-5:], ensure_ascii=False)
                    )
                outputs = entry.get("outputs") or {}
                if outputs and (status_str == "success" or status.get("completed") is True):
                    return self._resolve_history_output(comfy_root, entry, started)

            time.sleep(2)

        raise RuntimeError("视频生成超时。")

    def _run_job(self, job_id: str, adapter_key: str, payload: dict) -> None:
        try:
            adapter = ADAPTERS[adapter_key]
            workflow_path = resource_path(adapter["workflow"])
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            workflow, keep = customize_workflow(workflow, adapter_key, payload, job_id)

            portable, _, _ = self._ensure_comfyui()
            self._ensure_models(portable, workflow, keep)
            self._ensure_comfyui_server()

            self._set_phase("building_workflow", "正在构建 ComfyUI 工作流")
            object_info = json_request(COMFY_BASE + "/object_info", timeout=60)
            api_prompt = workflow_to_api_prompt(workflow, object_info, keep)

            self._set_phase("queued", "正在提交视频任务")
            prompt_id = self._queue_prompt(api_prompt)
            with self.lock:
                self.prompt_id = prompt_id

            self._set_phase("generating", "ComfyUI 正在生成视频")
            generation_started = time.time()
            source = self._wait_for_result(portable, prompt_id, generation_started)
            if self.cancel.is_set():
                raise RuntimeError("任务已取消。")

            suffix = source.suffix.lower() or ".mp4"
            output = VIDEO_OUTPUT_ROOT / f"{job_id}{suffix}"
            shutil.copy2(source, output)

            with self.lock:
                if self.cancel.is_set():
                    output.unlink(missing_ok=True)
                    raise RuntimeError("任务已取消。")
                self.output_path = str(output)
                self.phase = "complete"
                self.detail = "视频生成完成"
                self.error = None
                self.finished_at = time.time()
                self.current_file = None
            self.log(f"Video complete: {output.name}")

        except Exception as error:
            # Timeout and local output failures must not leave the owned GPU
            # prompt running after this worker releases the generation slot.
            if self.prompt_id:
                self._cancel_prompt(self.prompt_id)
            with self.lock:
                if self.cancel.is_set():
                    self.phase = "cancelled"
                    self.detail = "任务已取消"
                else:
                    self.phase = "failed"
                    self.detail = "视频任务失败"
                self.error = str(error)
                self.finished_at = time.time()
            self.log("Video error: " + repr(error))
