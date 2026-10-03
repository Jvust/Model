"""Shared ComfyUI isolation regressions; fixture files only, no model downloads/GPU."""
import io
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from runtime import image_runtime as image
from runtime import video_runtime as video


class RuntimeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = video.VideoRuntime.__new__(video.VideoRuntime)
        self.image = image.ImageRuntime.__new__(image.ImageRuntime)
        for runtime in (self.video, self.image):
            runtime.lock = threading.RLock()
            runtime.cancel = threading.Event()
            runtime.job_thread = None
            runtime.logs = deque(maxlen=30)
            runtime.reset_state()
            runtime.job_id = "own-job"
        self.video.process = None
        self.video.adapter = "wan2.2-ti2v-5b"
        self.image.adapter = "pony_diffusion_v6_xl"
        self.image.comfy = self.video
        self.image.token_provider = Mock(return_value="fixture")

    def image_file(self, name="own.png", data=b"fixture-image"):
        path = self.root / "ComfyUI" / "output" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def history(self, name="own.png", subfolder="", kind="output", node="8"):
        return {"outputs": {node: {"images": [{"filename": name, "subfolder": subfolder, "type": kind}]}},
                "status": {"status_str": "success", "completed": True}}

    def alive_worker(self, runtime):
        release = threading.Event()
        thread = threading.Thread(target=release.wait, daemon=True)
        runtime.job_thread = thread
        thread.start()
        self.addCleanup(lambda: (release.set(), thread.join(timeout=2)))
        return release

    def test_idle_image_stop_does_not_cancel_video_or_interrupt_server(self):
        with patch.object(image, "json_request") as request, patch.object(image, "managed_comfy_hardware", return_value={}):
            self.image.stop()
        self.assertFalse(self.video.cancel.is_set())
        request.assert_not_called()

    def test_image_cancellation_remains_busy_until_worker_exits(self):
        self.alive_worker(self.image)
        self.image.phase = "generating"
        with patch.object(image, "json_request"), patch.object(image, "managed_comfy_hardware", return_value={}):
            state = self.image.stop()
        self.assertTrue(state["running"])
        self.assertEqual(state["phase"], "cancelling")

    def test_idle_video_stop_does_not_cancel_borrowed_image_preparation(self):
        self.alive_worker(self.image)
        self.video.phase = "downloading_models"
        with patch.object(video, "json_request") as request:
            self.video.stop()
        self.assertFalse(self.video.cancel.is_set())
        request.assert_not_called()

    def test_queue_delete_failure_still_attempts_targeted_interrupt(self):
        for module, runtime in ((video, self.video), (image, self.image)):
            with self.subTest(module=module.__name__), patch.object(module, "json_request", side_effect=[OSError("fixture queue unavailable"), {}]) as request:
                runtime._cancel_prompt("own-prompt")
                self.assertEqual(request.call_count, 2)
                self.assertTrue(request.call_args.args[0].endswith("/api/jobs/own-prompt/cancel"))

    def test_video_interrupt_targets_owned_prompt(self):
        self.alive_worker(self.video)
        self.video.phase = "generating"
        self.video.prompt_id = "own-prompt"
        with patch.object(video, "json_request") as request:
            self.video.stop()
        interrupt = [call for call in request.call_args_list if call.args[0].endswith("/api/jobs/own-prompt/cancel")]
        self.assertEqual(len(interrupt), 1)
        self.assertFalse(any(call.args[0].endswith("/interrupt") for call in request.call_args_list))

    def test_image_interrupt_targets_owned_prompt(self):
        self.alive_worker(self.image)
        self.image.phase = "generating"
        self.image.prompt_id = "own-prompt"
        with patch.object(image, "json_request") as request, patch.object(image, "managed_comfy_hardware", return_value={}):
            self.image.stop()
        interrupt = [call for call in request.call_args_list if call.args[0].endswith("/api/jobs/own-prompt/cancel")]
        self.assertEqual(len(interrupt), 1)
        self.assertFalse(any(call.args[0].endswith("/interrupt") for call in request.call_args_list))

    def test_unsupported_cancel_endpoint_never_falls_back_to_global_interrupt(self):
        for module, runtime in ((video, self.video), (image, self.image)):
            with self.subTest(module=module.__name__), patch.object(module, "json_request", side_effect=OSError("fixture 404")) as request:
                runtime._cancel_prompt("own-prompt")
            self.assertFalse(any(call.args[0].endswith("/interrupt") for call in request.call_args_list))
            self.assertTrue(any("v0.37.0" in log for log in runtime.logs))

    def test_busy_video_cancel_event_is_not_cleared_by_image_start(self):
        self.alive_worker(self.video)
        self.video.phase = "cancelling"
        self.video.cancel.set()
        with patch.object(image, "managed_comfy_hardware", return_value={"supported": True}), self.assertRaises(RuntimeError):
            self.image.start({"model_id": "pony_diffusion_v6_xl"})
        self.assertTrue(self.video.cancel.is_set())

    def test_image_history_does_not_fall_back_to_unrelated_recent_file(self):
        self.image_file("other-job.png")
        with self.assertRaises(RuntimeError):
            self.image._resolve_image(self.root, {"outputs": {}}, time.time())

    def test_image_history_rejects_traversal(self):
        outside = self.root / "private.png"
        outside.write_bytes(b"private")
        with self.assertRaises(ValueError):
            self.image._resolve_image(self.root, self.history("private.png", "../../"), time.time())

    def test_image_history_rejects_input_directory(self):
        path = self.root / "ComfyUI" / "input" / "own.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
        with self.assertRaises(ValueError):
            self.image._resolve_image(self.root, self.history(kind="input"), time.time())

    def test_image_history_rejects_wrong_node_and_empty_output(self):
        self.image_file()
        with self.assertRaises(RuntimeError):
            self.image._resolve_image(self.root, self.history(node="unrelated"), time.time())
        self.image_file(data=b"")
        with self.assertRaises(RuntimeError):
            self.image._resolve_image(self.root, self.history(), time.time())

    def test_image_history_waits_for_success(self):
        interim = {**self.history(), "status": {"status_str": "running", "completed": False}}
        self.image._history = Mock(side_effect=[interim, self.history()])
        self.image._resolve_image = Mock(return_value=Path("fixture.png"))
        with patch.object(image.time, "sleep"):
            self.image._wait_for_result(self.root, "own-prompt", time.time())
        self.assertEqual(self.image._history.call_count, 2)

    def test_image_history_accepts_own_nonempty_supported_output(self):
        path = self.image_file()
        self.assertEqual(self.image._resolve_image(self.root, self.history(), time.time()), path.resolve())
        self.image.adapter = "flux2_klein_4b_fp8"
        self.assertEqual(self.image._resolve_image(self.root, self.history(node="13"), time.time()), path.resolve())

    def test_image_history_rejects_nonimage_output(self):
        self.image_file("private.txt")
        with self.assertRaises(ValueError):
            self.image._resolve_image(self.root, self.history("private.txt"), time.time())

    def test_image_output_requires_completed_owned_job(self):
        path = self.image_file()
        self.image.output_path = str(path)
        with patch.object(image, "IMAGE_OUTPUT_ROOT", path.parent), self.assertRaises(FileNotFoundError):
            self.image.output_file("own-job")

    def test_image_phase_cannot_advance_after_stop(self):
        self.image.cancel.set()
        with self.assertRaises(RuntimeError):
            self.image._set_phase("queued", "fixture")

    def run_image_fixture(self, cancel_after_result=False, fail_result=False, cancel_during_queue=False):
        path = self.image_file()
        self.image._install_artifact = Mock(return_value=image.PONY_CHECKPOINT)
        self.image._verify_required_nodes = Mock()
        self.video._ensure_comfyui = Mock(return_value=(self.root, None, None))
        self.video._ensure_comfyui_server = Mock()
        def queue(_prompt):
            if cancel_during_queue:
                self.image.cancel.set()
            return "own-prompt"
        self.video._queue_prompt = Mock(side_effect=queue)
        def result(*_args):
            if cancel_after_result:
                self.image.cancel.set()
            if fail_result:
                raise RuntimeError("fixture timeout")
            return path
        self.image._wait_for_result = Mock(side_effect=result)
        with patch.object(image, "artifact_specs", return_value={"checkpoint": object()}), patch.object(image, "json_request", return_value={}) as request, patch.object(image, "IMAGE_OUTPUT_ROOT", self.root):
            self.image._run_job("own-job", "pony_diffusion_v6_xl", {"prompt": "a glass sculpture", "seed": 0})
        return request

    def test_image_fixture_completes_with_owned_output(self):
        self.run_image_fixture()
        self.assertEqual(self.image.phase, "complete")
        with patch.object(image, "IMAGE_OUTPUT_ROOT", self.root):
            self.assertEqual(self.image.output_file("own-job").name, "own-job.png")

    def test_image_cancelled_result_cannot_become_complete(self):
        request = self.run_image_fixture(cancel_after_result=True)
        self.assertEqual(self.image.phase, "cancelled")
        self.assertIsNone(self.image.output_path)
        self.assertTrue(any(call.args[0].endswith("/api/jobs/own-prompt/cancel") for call in request.call_args_list))

    def test_image_queue_response_after_stop_is_removed(self):
        request = self.run_image_fixture(cancel_during_queue=True)
        self.assertEqual(self.image.phase, "cancelled")
        self.image._wait_for_result.assert_not_called()
        self.assertTrue(any(call.kwargs.get("payload") == {"delete": ["own-prompt"]} for call in request.call_args_list))

    def test_image_timeout_cleans_owned_backend_job(self):
        request = self.run_image_fixture(fail_result=True)
        self.assertEqual(self.image.phase, "failed")
        self.assertTrue(any(call.args[0].endswith("/api/jobs/own-prompt/cancel") for call in request.call_args_list))

    def response(self, content=b"CD", start=2, end=3, total=4, status=206):
        response = io.BytesIO(content)
        response.headers = {"Content-Length": str(len(content)), "Content-Range": f"bytes {start}-{end}/{total}"}
        response.status = status
        return response

    def test_resume_rejects_wrong_range_start_without_corrupting_partial(self):
        path = self.root / "weights.bin"
        partial = path.with_suffix(".bin.part")
        partial.write_bytes(b"AB")
        with patch.object(video, "urlopen", return_value=self.response(start=0)), patch.object(video.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
            with self.assertRaises(RuntimeError):
                self.video._download("https://fixture.invalid/model", path, "fixture")
        self.assertFalse(path.exists())
        self.assertEqual(partial.read_bytes(), b"AB")

    def test_resume_valid_range_produces_complete_file(self):
        path = self.root / "weights.bin"
        path.with_suffix(".bin.part").write_bytes(b"AB")
        with patch.object(video, "urlopen", return_value=self.response()), patch.object(video.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
            self.video._download("https://fixture.invalid/model", path, "fixture")
        self.assertEqual(path.read_bytes(), b"ABCD")

    def test_unsolicited_nonzero_partial_response_is_rejected(self):
        path = self.root / "weights.bin"
        with patch.object(video, "urlopen", return_value=self.response(start=2)), patch.object(video.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
            with self.assertRaises(RuntimeError):
                self.video._download("https://fixture.invalid/model", path, "fixture")
        self.assertFalse(path.exists())

    def test_missing_range_header_does_not_mutate_partial(self):
        path = self.root / "weights.bin"
        partial = path.with_suffix(".bin.part")
        partial.write_bytes(b"AB")
        response = self.response()
        response.headers.pop("Content-Range")
        with patch.object(video, "urlopen", return_value=response), self.assertRaises(RuntimeError):
            self.video._download("https://fixture.invalid/model", path, "fixture")
        self.assertEqual(partial.read_bytes(), b"AB")

    def test_ignored_range_starts_fresh_not_appended(self):
        path = self.root / "weights.bin"
        path.with_suffix(".bin.part").write_bytes(b"obsolete")
        response = self.response(content=b"ABCD", start=0, status=200)
        response.headers.pop("Content-Range")
        with patch.object(video, "urlopen", return_value=response), patch.object(video.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
            self.video._download("https://fixture.invalid/model", path, "fixture")
        self.assertEqual(path.read_bytes(), b"ABCD")


if __name__ == "__main__":
    unittest.main()
