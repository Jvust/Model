"""Release composition root: legacy engines plus isolated native-task execution."""
from __future__ import annotations
import json
import mimetypes
import sys
from pathlib import Path
import re
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote
try:
    from . import local_bridge as bridge
    from .native_runtime import NativeRuntime
    from .activation_runtime import ActivationRuntime
    from .activation_backends import ActivationBackends
    from .instance_lock import instance_lock
    from . import conversation_store
    from .native_worker import CATALOG
    from .desktop_source import DesktopSource, DesktopAwareCache, payload_specs, PREFIX
    from .chat_profiles import ProfiledRuntimeState, load_profile, save_profile
except ImportError:
    import local_bridge as bridge
    from native_runtime import NativeRuntime
    from activation_runtime import ActivationRuntime
    from activation_backends import ActivationBackends
    from instance_lock import instance_lock
    import conversation_store
    from native_worker import CATALOG
    from desktop_source import DesktopSource, DesktopAwareCache, payload_specs, PREFIX
    from chat_profiles import ProfiledRuntimeState, load_profile, save_profile

VERSION = 19
DESKTOP = DesktopSource(bridge.DRIVE_CACHE.root)
bridge.DRIVE_CACHE = DesktopAwareCache(bridge.DRIVE_CACHE.root, DESKTOP)
for engine in (bridge.IMAGE, bridge.TASK, bridge.PACKAGE):
    engine.drive_cache = bridge.DRIVE_CACHE
bridge.STATE = ProfiledRuntimeState()
bridge.ALLOWED_ORIGINS.update({f"http://127.0.0.1:{bridge.BRIDGE_PORT}", f"http://localhost:{bridge.BRIDGE_PORT}"})
SITE_ROOT = Path(sys._MEIPASS) / "site" if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
NATIVE = NativeRuntime(bridge.DRIVE_CACHE, bridge.DRIVE_SESSION.get)
ACTIVATION = ActivationRuntime(bridge.DRIVE_CACHE.root, ActivationBackends(bridge, NATIVE, DESKTOP), recover=False)


def byte_range(header, size):
    if not header:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not size or not any(match.groups()):
        raise ValueError("Unsatisfiable range")
    left, right = match.groups()
    if not left:
        length = int(right)
        if length <= 0:
            raise ValueError("Invalid suffix range")
        return max(0, size - length), size - 1, 206
    start, end = int(left), min(int(right), size - 1) if right else size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, end, 206


class ApplicationHandler(bridge.Handler):
    server_version = "DriveModelBridge/0.19-candidate"

    def _gate(self):
        if not self._origin_allowed():
            self._json(403, {"error": "Origin not allowed."})
            return False
        if not self._authorized():
            self._json(401, {"error": "Runtime authorization required."})
            return False
        return True

    def _serve_video_file(self, path):
        size = path.stat().st_size
        try:
            start, end, status = byte_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self._cors_headers()
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", {".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".webm": "video/webm", ".gif": "image/gif", ".mkv": "video/x-matroska"}.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = max(0, end - start + 1)
            while remaining:
                chunk = source.read(min(remaining, 1024**2))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in {"/", "/index.html", "/sw.js"} or path.startswith(("/assets/", "/docs/", "/notebooks/")):
            relative = "index.html" if path == "/" else unquote(path).lstrip("/")
            if "\x00" in relative or "\\" in relative or any(part in {".", ".."} for part in relative.split("/")):
                self._json(404, {"error": "Invalid static path"})
                return
            file = (SITE_ROOT / relative).resolve()
            if not file.is_relative_to(SITE_ROOT.resolve()) or not file.is_file():
                self._json(404, {"error": "Static file not found"})
                return
            content = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(content)
            return
        if path == "/health":
            self._json(200, {"ok": True, "version": VERSION, "service": "Drive Model Local Runtime", "remote_auth_required": bool(bridge.REMOTE_TOKEN)})
            return
        if path.startswith("/v1/") and not self._gate():
            return
        if path == "/v1/chat/history":
            self._json(200, conversation_store.read(ACTIVATION.store, bridge.STATE.snapshot()))
            return
        if path.startswith("/v1/activation/"):
            self._activation_get(path)
            return
        if path == "/v1/desktop/status":
            self._json(200, DESKTOP.status())
        elif path == "/v1/desktop/snapshot":
            with DESKTOP.lock:
                snapshot = DESKTOP.snapshot_data
            if snapshot is None:
                self._json(409, {"error": "请先完成当前 Runtime 的桌面版目录扫描"})
            else:
                self._json(200, snapshot)
        elif path == "/v1/native/catalog":
            self._json(200, {"models": [{"model_id": key, **value, "execution_verified": False} for key, value in CATALOG.items()]})
        elif path == "/v1/native/status":
            self._json(200, NATIVE.snapshot())
        elif path in {"/v1/native/result", "/v1/native/media"}:
            try:
                job_id = parse_qs(urlparse(self.path).query).get("job_id", [""])[0]
                result = NATIVE.result_path(job_id, media=path.endswith("media"))
                if path.endswith("media"):
                    self._serve_video_file(result)
                else:
                    self._json(200, json.loads(result.read_text(encoding="utf-8")))
            except ValueError as error:
                self._json(400, {"error": str(error)})
            except FileNotFoundError as error:
                self._json(404, {"error": str(error)})
        elif path == "/v1/runtime":
            state = bridge.STATE.snapshot()
            state.update(runtime_version=VERSION, drive_api_session=bool(bridge.DRIVE_SESSION.access_token), cache_root_label=bridge.DRIVE_CACHE.root.name, llama_server_found=bridge.resolve_llama_server() is not None, bridge_port=bridge.BRIDGE_PORT, model_server_port=bridge.MODEL_SERVER_PORT, video=bridge.VIDEO.snapshot(), image=bridge.IMAGE.snapshot(), task=bridge.TASK.snapshot(), package=bridge.PACKAGE.snapshot(), native=NATIVE.snapshot(), activation=ACTIVATION.snapshot(), desktop=DESKTOP.status(), hardware=bridge.runtime_hardware_snapshot(), remote_auth_required=bool(bridge.REMOTE_TOKEN))
            self._json(200, state)
        else:
            super().do_GET()

    def _activation_get(self, path):
        try:
            query = parse_qs(urlparse(self.path).query)
            if path == "/v1/activation/history":
                self._json(200, {"jobs": ACTIVATION.store.history()})
            elif path == "/v1/activation/status":
                self._json(200, ACTIVATION.snapshot())
            elif path == "/v1/activation/job":
                self._json(200, ACTIVATION.store.get(query.get("job_id", [""])[0]))
            elif path == "/v1/activation/file":
                result = ACTIVATION.media_path(query.get("job_id", [""])[0], query.get("step", [""])[0], query.get("name", [""])[0])
                self._serve_video_file(result)
            else:
                self._json(404, {"error": "Unknown activation route"})
        except FileNotFoundError as error:
            self._json(404, {"error": str(error)})
        except (ValueError, TypeError, KeyError, OSError) as error:
            self._json(400, {"error": str(error)})

    def _activation_post(self, path):
        if not self._gate():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 16_000_000:
                raise ValueError("Activation request exceeds 16 MB")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            allowed = {
                "/v1/activation/plan": {"selection"},
                "/v1/activation/start": {"selection", "input", "items", "prepare_only", "accept_variant", "request_key"},
                "/v1/activation/resume": {"selection", "job_id"},
                "/v1/activation/stop": set(),
                "/v1/activation/workspace": {"selection", "value"},
            }
            if path not in allowed:
                self._json(404, {"error": "Unknown activation route"})
                return
            if set(body) - allowed[path]:
                raise ValueError("Unexpected activation request fields")
            if path.endswith("/plan"):
                result = ACTIVATION.plan(body.get("selection"))
            elif path.endswith("/start"):
                result = ACTIVATION.start(body)
            elif path.endswith("/resume"):
                result = ACTIVATION.resume(body)
            elif path.endswith("/stop"):
                result = ACTIVATION.stop()
            else:
                plan = ACTIVATION.plan(body.get("selection"))
                if "value" in body:
                    # Workspaces store settings, not file payloads, handles, or credentials.
                    value = body["value"]
                    allowed_settings = {"prompt", "negative_prompt", "width", "height", "steps", "cfg", "seed", "batch_prompts", "horizon", "frequency", "max_new_tokens", "format", "top_n"}
                    if not isinstance(value, dict) or set(value) - allowed_settings:
                        raise ValueError("Only prompt and task settings may be saved in the workspace")
                    ACTIVATION.store.save_workspace(plan["identity"], value)
                result = {"identity": plan["identity"], "value": ACTIVATION.store.load_workspace(plan["identity"])}
            self._json(202 if path.endswith(("/start", "/resume")) else 200, result)
        except PermissionError as error:
            self._json(401, {"error": str(error)})
        except FileNotFoundError as error:
            self._json(404, {"error": str(error)})
        except (ValueError, TypeError, KeyError, OSError) as error:
            self._json(400, {"error": str(error)})
        except RuntimeError as error:
            self._json(409, {"error": str(error)})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/v1/chat/history":
            if not self._gate():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 1_500_000:
                    raise ValueError("Chat history request is too large")
                body = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(body, dict) or set(body) != {"identity", "messages"}:
                    raise ValueError("Invalid chat history request")
                self._json(200, conversation_store.write(ACTIVATION.store, bridge.STATE.snapshot(), body["identity"], body["messages"]))
            except (TypeError, ValueError, KeyError) as error:
                self._json(400, {"error": str(error)})
            return
        if path.startswith("/v1/activation/"):
            self._activation_post(path)
            return
        exclusive = {
            "/v1/models/start", "/v1/models/stop", "/v1/models/cache/clear", "/v1/models/cache/delete",
            "/v1/image/generate", "/v1/image/stop", "/v1/video/generate", "/v1/video/stop",
            "/v1/packages/materialize", "/v1/packages/stop", "/v1/tasks/start", "/v1/tasks/stop",
            "/v1/tasks/embeddings", "/v1/tasks/rerank", "/v1/chat/completions", "/v1/native/start",
            "/v1/native/stop", "/v1/desktop/pick", "/v1/desktop/scan", "/v1/desktop/stop",
        }
        if path in exclusive:
            if not self._gate():
                return
            with ACTIVATION.lock:
                if ACTIVATION.active():
                    self._json(409, {"error": "统一启用任务正在进行，请使用任务面板取消，不能同时更改来源/清缓存/启动另一个后端"})
                    return
                self._do_post_legacy()
        else:
            self._do_post_legacy()

    def _do_post_legacy(self):
        path = urlparse(self.path).path
        if path.startswith("/v1/desktop/"):
            if not self._gate():
                return
            try:
                data = self._read_json()
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                # The network API never accepts an arbitrary host path.
                if any(key in data for key in ("root", "path", "directory", "config_path")):
                    raise ValueError("请使用 Runtime 电脑上的原生目录选择器；API 不接受任意本机路径")
                if path in {"/v1/desktop/pick", "/v1/desktop/scan"}:
                    states = [bridge.STATE.snapshot(), bridge.TASK.snapshot(), bridge.IMAGE.snapshot(), bridge.VIDEO.snapshot(), bridge.PACKAGE.snapshot(), NATIVE.snapshot()]
                    if any(state.get("running") or state.get("phase") in {"downloading", "loading", "starting", "cancelling"} for state in states):
                        raise RuntimeError("请先停止当前模型和任务，再更换或扫描桌面版目录")
                if path == "/v1/desktop/pick":
                    hosts = {f"127.0.0.1:{bridge.BRIDGE_PORT}", f"localhost:{bridge.BRIDGE_PORT}"}
                    origins = {"http://" + host for host in hosts}
                    if self.client_address[0] != "127.0.0.1" or self.headers.get("Host") not in hosts or self.headers.get("Origin") not in origins or self.headers.get("X-Forwarded-Host") or self.headers.get("Forwarded"):
                        self._json(403, {"error": "目录授权只能从该 Windows 主机的本机网页发起"})
                        return
                    self._json(202, DESKTOP.pick())
                elif path == "/v1/desktop/scan":
                    self._json(202, DESKTOP.start_scan())
                elif path == "/v1/desktop/stop":
                    self._json(200, DESKTOP.stop())
                elif path == "/v1/desktop/probe":
                    specs = payload_specs(data)
                    if len(specs) != 1 or not specs[0].file_id.startswith(PREFIX):
                        raise ValueError("One scanned desktop file is required")
                    item = DESKTOP.resolve(specs[0])
                    with item.checked_path().open("rb") as stream:
                        count = len(stream.read(4096))
                    item.checked_path()
                    self._json(200, {"ok": True, "bytes": count, "source": "desktop", "full_file_verified": False})
                else:
                    self._json(404, {"error": "Unknown desktop route"})
            except (ValueError, TypeError, KeyError, OSError) as error:
                self._json(400, {"error": str(error)})
            except RuntimeError as error:
                self._json(409, {"error": str(error)})
            return
        if path == "/v1/chat/profile":
            if not self._gate():
                return
            try:
                data = self._read_json()
                name, relative = data.get("name"), data.get("relative_path", "")
                profile = save_profile(name, relative, data["profile"]) if "profile" in data else load_profile(name, relative)
                self._json(200, {"profile": profile})
            except (ValueError, TypeError, KeyError) as error:
                self._json(400, {"error": str(error)})
            return
        if not path.startswith("/v1/native/"):
            super().do_POST()
            return
        if not self._gate():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= 16_000_000:
                raise ValueError("Native request body exceeds 16 MB")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            if path == "/v1/native/plan":
                self._json(200, NATIVE.plan(payload))
            elif path == "/v1/native/start":
                self._json(202, NATIVE.start(payload))
            elif path == "/v1/native/stop":
                self._json(200, NATIVE.stop())
            else:
                self._json(404, {"error": "Unknown native route"})
        except (ValueError, TypeError) as error:
            self._json(400, {"error": str(error)})
        except RuntimeError as error:
            self._json(409, {"error": str(error)})
        except Exception as error:
            self._json(500, {"error": str(error)})


def serve():
    server = ThreadingHTTPServer((bridge.HOST, bridge.BRIDGE_PORT), ApplicationHandler)
    ACTIVATION.store.recover()
    print(f"Drive Model Runtime 0.19 candidate · {bridge.HOST}:{bridge.BRIDGE_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ACTIVATION.shutdown()
        DESKTOP.stop()
        NATIVE.stop()
        if NATIVE.thread:
            NATIVE.thread.join(timeout=10)
        bridge.PACKAGE.stop()
        bridge.TASK.stop()
        bridge.IMAGE.shutdown()
        bridge.VIDEO.shutdown()
        bridge.STATE.stop()
        server.server_close()


def main():
    with instance_lock(bridge.DRIVE_CACHE.root):
        serve()


if __name__ == "__main__":
    main()
