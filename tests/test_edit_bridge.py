"""Real localhost HTTP contract tests; inference itself remains a fixture."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from runtime import local_bridge as bridge


class EditBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.edit = SimpleNamespace(
            snapshot=Mock(return_value={"running": False, "phase": "idle"}),
            environment=Mock(return_value={"supported": False, "detail": "fixture missing CUDA", "exit_status": 2}),
            start=Mock(return_value={"phase": "starting", "running": True}),
            stop=Mock(return_value={"phase": "cancelled", "running": False}),
            output_file=Mock(side_effect=FileNotFoundError("not ready")),
        )
        self.image = SimpleNamespace(snapshot=Mock(return_value={"running": False}), start=Mock(return_value={"running": True}))
        self.video = SimpleNamespace(snapshot=Mock(return_value={"running": False}), start=Mock(return_value={"running": True}))
        self.state = SimpleNamespace(snapshot=Mock(return_value={"running": False}), append_log=Mock())
        for key, value in (("EDIT", self.edit), ("IMAGE", self.image), ("VIDEO", self.video), ("STATE", self.state), ("REMOTE_TOKEN", "fixture-secret")):
            self.stack.enter_context(patch.object(bridge, key, value))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path: str, payload=None, authorized=True, origin="https://jvust1.github.io"):
        headers = {"Origin": origin}
        if authorized:
            headers["Authorization"] = "Bearer fixture-secret"
        data = None if payload is None else json.dumps(payload).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"http://127.0.0.1:{self.server.server_port}" + path, data=data, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_health_is_v17(self) -> None:
        status, _, body = self.request("/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], 17)

    def test_account_origin_and_authorized_status(self) -> None:
        status, headers, body = self.request("/v1/edit/status")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://jvust1.github.io")
        self.assertEqual(json.loads(body)["phase"], "idle")

    def test_all_edit_endpoints_keep_token_check(self) -> None:
        for path, payload in (("/v1/edit/status", None), ("/v1/edit/environment", None), ("/v1/edit/file?job_id=x", None), ("/v1/edit/generate", {}), ("/v1/edit/stop", {})):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, payload, authorized=False)[0], 401)
        self.edit.start.assert_not_called()
        self.edit.output_file.assert_not_called()

    def test_wrong_origin_is_rejected(self) -> None:
        self.assertEqual(self.request("/v1/edit/generate", {}, origin="https://untrusted.invalid")[0], 403)
        self.edit.start.assert_not_called()

    def test_video_result_now_requires_remote_authorization(self) -> None:
        self.assertEqual(self.request("/v1/video/file?job_id=fixture", authorized=False)[0], 401)

    def test_environment_is_not_falsely_ready(self) -> None:
        status, _, body = self.request("/v1/edit/environment")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["supported"])

    def test_missing_output_is_404(self) -> None:
        self.assertEqual(self.request("/v1/edit/file?job_id=fixture")[0], 404)

    def test_output_is_png_not_public_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            self.edit.output_file.side_effect = None
            self.edit.output_file.return_value = path
            status, headers, body = self.request("/v1/edit/file?job_id=fixture")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(body, path.read_bytes())

    def test_larger_json_is_edit_only(self) -> None:
        payload = {"reference_image": "fixture" * 45000}
        self.assertEqual(self.request("/v1/edit/generate", payload)[0], 202)
        self.assertEqual(self.request("/v1/image/generate", payload)[0], 400)
        self.image.start.assert_not_called()

    def test_validation_error_is_400(self) -> None:
        self.edit.start.side_effect = ValueError("unsupported operation")
        self.assertEqual(self.request("/v1/edit/generate", {"operation": "invalid"})[0], 400)

    def test_existing_worker_blocks_edit(self) -> None:
        for worker in (self.image, self.video, self.state):
            with self.subTest(worker=worker):
                worker.snapshot.return_value = {"running": True}
                self.assertEqual(self.request("/v1/edit/generate", {})[0], 400)
                worker.snapshot.return_value = {"running": False}
        self.edit.start.assert_not_called()

    def test_edit_blocks_image_and_video(self) -> None:
        self.edit.snapshot.return_value = {"running": True}
        for path in ("/v1/image/generate", "/v1/video/generate"):
            self.assertEqual(self.request(path, {})[0], 400)
        self.image.start.assert_not_called()
        self.video.start.assert_not_called()

    def test_start_arbitration_is_atomic(self) -> None:
        entered, release = threading.Event(), threading.Event()
        def start_edit(_payload):
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("fixture timeout")
            self.edit.snapshot.return_value = {"running": True}
            return {"running": True, "phase": "starting"}
        self.edit.start.side_effect = start_edit
        with ThreadPoolExecutor(max_workers=2) as pool:
            edit = pool.submit(self.request, "/v1/edit/generate", {})
            self.assertTrue(entered.wait(timeout=2))
            image = pool.submit(self.request, "/v1/image/generate", {})
            time.sleep(.03)
            release.set()
            self.assertEqual(edit.result(timeout=5)[0], 202)
            self.assertEqual(image.result(timeout=5)[0], 400)
        self.image.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
