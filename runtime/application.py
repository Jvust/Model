"""Release composition root: legacy engines plus isolated native-task execution."""
from __future__ import annotations
import json
import re
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
try:
    from . import local_bridge as bridge
    from .native_runtime import NativeRuntime
    from .native_worker import CATALOG
except ImportError:
    import local_bridge as bridge
    from native_runtime import NativeRuntime
    from native_worker import CATALOG

VERSION = 17
NATIVE = NativeRuntime(bridge.DRIVE_CACHE, bridge.DRIVE_SESSION.get)


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
    server_version = "DriveModelBridge/0.17"

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
        self.send_header("Content-Type", {".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".webm": "video/webm"}.get(path.suffix.lower(), "application/octet-stream"))
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
        if path == "/health":
            self._json(200, {"ok": True, "version": VERSION, "service": "Drive Model Runtime", "remote_auth_required": bool(bridge.REMOTE_TOKEN)})
            return
        if path.startswith("/v1/") and not self._gate():
            return
        if path == "/v1/native/catalog":
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
            state.update(runtime_version=VERSION, drive_api_session=bool(bridge.DRIVE_SESSION.access_token), cache_root_label=bridge.DRIVE_CACHE.root.name, llama_server_found=bridge.resolve_llama_server() is not None, bridge_port=bridge.BRIDGE_PORT, model_server_port=bridge.MODEL_SERVER_PORT, video=bridge.VIDEO.snapshot(), image=bridge.IMAGE.snapshot(), task=bridge.TASK.snapshot(), package=bridge.PACKAGE.snapshot(), native=NATIVE.snapshot(), hardware=bridge.runtime_hardware_snapshot(), remote_auth_required=bool(bridge.REMOTE_TOKEN))
            self._json(200, state)
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
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


def main():
    server = ThreadingHTTPServer((bridge.HOST, bridge.BRIDGE_PORT), ApplicationHandler)
    print(f"Drive Model Runtime 0.17 · {bridge.HOST}:{bridge.BRIDGE_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        NATIVE.stop()
        if NATIVE.thread:
            NATIVE.thread.join(timeout=10)
        bridge.PACKAGE.stop()
        bridge.TASK.stop()
        bridge.IMAGE.shutdown()
        bridge.VIDEO.shutdown()
        bridge.STATE.stop()
        server.server_close()


if __name__ == "__main__":
    main()
