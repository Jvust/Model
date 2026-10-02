"""No GPU downloads: exercise real state methods with deterministic backend fixtures."""
import io
import json
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from runtime import video_runtime as video


class VideoValidationTests(unittest.TestCase):
    def validate(self, **changes):
        return video.validate_video_payload("wan2.2-ti2v-5b", {"prompt": "waves at sunrise", **changes})

    def test_zero_cfg_and_seed_are_preserved(self):
        result = self.validate(cfg=0, seed=0)
        self.assertEqual((result["cfg"], result["seed"]), (0, 0))

    def test_fractional_and_boolean_inputs_rejected(self):
        for key in ("width", "height", "frames", "steps", "fps", "seed"):
            for value in (1.5, True):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.validate(**{key: value})

    def test_zero_out_of_range_and_alignment_rejected(self):
        for change in ({"steps": 0}, {"fps": 0}, {"width": 257}, {"height": 257}, {"frames": 50}, {"seed": -1}, {"seed": 2**53}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(**change)

    def test_nonfinite_cfg_rejected(self):
        for value in (float("nan"), float("inf"), -1, 21, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(cfg=value)

    def test_prompt_bounds(self):
        for change in ({"prompt": " "}, {"prompt": 123}, {"prompt": "a"*12001}, {"negative_prompt": "a"*6001}):
            with self.subTest(change=str(change)[:30]), self.assertRaises(ValueError):
                self.validate(**change)

    def test_hunyuan_resolution_is_server_enforced(self):
        good = video.validate_video_payload("hunyuanvideo-1.5", {"prompt": "waves"})
        self.assertEqual((good["width"], good["height"]), (1280, 720))
        with self.assertRaises(ValueError):
            video.validate_video_payload("hunyuanvideo-1.5", {"prompt": "waves", "width": 832, "height": 480})


class VideoStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = video.VideoRuntime.__new__(video.VideoRuntime)
        self.runtime.lock = threading.RLock()
        self.runtime.cancel = threading.Event()
        self.runtime.job_thread = None
        self.runtime.process = None
        self.runtime.logs = deque(maxlen=30)
        self.runtime.reset_state()
        self.runtime.adapter = "wan2.2-ti2v-5b"
        self.runtime.job_id = "fixture-job"

    def make_output(self, name="fixture.mp4", data=b"fixture-video"):
        path = self.root / "ComfyUI" / "output" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def history(self, name="fixture.mp4", subfolder="", kind="output"):
        return {"outputs": {"58": {"videos": [{"filename": name, "subfolder": subfolder, "type": kind}]}}, "status": {"status_str": "success", "completed": True}}

    def test_validation_precedes_hardware_and_download(self):
        with patch.object(video, "adapter_hardware") as gate, self.assertRaises(ValueError):
            self.runtime.start({"name": "Wan2.2-TI2V-5B", "prompt": "waves", "frames": 50})
        gate.assert_not_called()

    def test_generation_does_not_show_stale_download_percentage(self):
        self.runtime.download_total_bytes = self.runtime.downloaded_bytes = 100
        self.runtime.phase = "generating"
        self.assertIsNone(self.runtime.snapshot()["download_progress"])
        self.runtime.phase = "downloading_models"
        self.assertEqual(self.runtime.snapshot()["download_progress"], 1)

    def test_snapshot_records_duration_elapsed_and_seed(self):
        self.runtime.parameters = {"frames": 49, "fps": 24, "seed": 123}
        self.runtime.started_at = time.time() - 2
        state = self.runtime.snapshot()
        self.assertEqual(state["duration_seconds"], 2.04)
        self.assertGreaterEqual(state["elapsed_seconds"], 2)
        self.assertEqual(state["parameters"]["seed"], 123)

    def test_idle_stop_does_not_interrupt_other_comfy_job(self):
        self.runtime.prompt_id = "old-completed-prompt"
        with patch.object(video, "json_request") as request:
            self.runtime.stop()
        request.assert_not_called()

    def test_cancelling_worker_stays_busy_until_actual_exit(self):
        release = threading.Event()
        thread = threading.Thread(target=release.wait, daemon=True)
        self.runtime.job_thread = thread
        self.runtime.phase = "generating"
        thread.start()
        try:
            state = self.runtime.stop()
            self.assertTrue(state["running"])
            self.assertEqual(state["phase"], "cancelling")
            with patch.object(video, "adapter_hardware", return_value={"supported": True}), self.assertRaises(RuntimeError):
                self.runtime.start({"name": "Wan2.2-TI2V-5B", "prompt": "waves"})
        finally:
            release.set()
            thread.join(timeout=2)
        self.assertFalse(self.runtime.stop()["running"])

    def test_output_from_expected_node(self):
        path = self.make_output()
        self.assertEqual(self.runtime._resolve_history_output(self.root, self.history(), time.time()), path.resolve())

    def test_no_unrelated_recent_file_fallback(self):
        self.make_output("another-job.mp4")
        with self.assertRaises(RuntimeError):
            self.runtime._resolve_history_output(self.root, {"outputs": {}}, time.time())

    def test_wrong_output_node_rejected(self):
        self.make_output()
        entry = self.history()
        entry["outputs"]["other"] = entry["outputs"].pop("58")
        with self.assertRaises(RuntimeError):
            self.runtime._resolve_history_output(self.root, entry, time.time())

    def test_history_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.runtime._resolve_history_output(self.root, self.history("private.mp4", "../../../"), time.time())

    def test_non_video_and_empty_output_rejected(self):
        self.make_output("fixture.jpg")
        with self.assertRaises(ValueError):
            self.runtime._resolve_history_output(self.root, self.history("fixture.jpg"), time.time())
        self.make_output(data=b"")
        with self.assertRaises(RuntimeError):
            self.runtime._resolve_history_output(self.root, self.history(), time.time())

    def test_current_completed_job_required(self):
        output = self.root / "own.mp4"
        output.write_bytes(b"fixture-video")
        self.runtime.output_path = str(output)
        with patch.object(video, "VIDEO_OUTPUT_ROOT", self.root):
            with self.assertRaises(FileNotFoundError):
                self.runtime.output_file("fixture-job")
            self.runtime.phase = "complete"
            self.assertEqual(self.runtime.output_file("fixture-job"), output)
            with self.assertRaises(FileNotFoundError):
                self.runtime.output_file("another-job")

    def test_history_waits_for_completion(self):
        intermediate = {**self.history(), "status": {"status_str": "running", "completed": False}}
        self.runtime._history = Mock(side_effect=[intermediate, self.history()])
        self.runtime._resolve_history_output = Mock(return_value=Path("fixture.mp4"))
        with patch.object(video.time, "sleep"):
            self.assertEqual(self.runtime._wait_for_result(self.root, "fixture-prompt", time.time()), Path("fixture.mp4"))
        self.assertEqual(self.runtime._history.call_count, 2)

    def download_response(self, content, length):
        response = io.BytesIO(content)
        response.headers = {"Content-Length": str(length)}
        response.status = 200
        return response

    def test_short_download_retains_partial_not_ready_file(self):
        destination = self.root / "model.safetensors"
        with patch.object(video, "urlopen", return_value=self.download_response(b"part", 8)), patch.object(video.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
            with self.assertRaisesRegex(RuntimeError, "下载未完成"):
                self.runtime._download("https://fixture.invalid/model", destination, "fixture")
        self.assertFalse(destination.exists())
        self.assertEqual(destination.with_suffix(".safetensors.part").read_bytes(), b"part")

    def test_complete_download_is_ready(self):
        destination = self.root / "model.safetensors"
        with patch.object(video, "urlopen", return_value=self.download_response(b"full", 4)), patch.object(video.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
            self.assertEqual(self.runtime._download("https://fixture.invalid/model", destination, "fixture"), destination)
        self.assertEqual(destination.read_bytes(), b"full")

    def run_fixture(self, cancelled=False, failed=False):
        self.runtime._ensure_comfyui = Mock(return_value=(self.root, None, None), side_effect=RuntimeError("fixture backend failure") if failed else None)
        self.runtime._ensure_models = Mock()
        self.runtime._ensure_comfyui_server = Mock()
        self.runtime._queue_prompt = Mock(return_value="fixture-prompt")
        path = self.make_output()
        def result(*_args):
            if cancelled:
                self.runtime.cancel.set()
            return path
        self.runtime._wait_for_result = Mock(side_effect=result)
        with patch.object(video, "json_request", return_value={}) as request, patch.object(video, "VIDEO_OUTPUT_ROOT", self.root):
            self.runtime._run_job("fixture-job", "wan2.2-ti2v-5b", {"prompt": "waves at sunrise", "seed": 0})
        return request

    def test_job_fixture_completes_with_owned_output(self):
        self.run_fixture()
        self.assertEqual(self.runtime.phase, "complete")
        with patch.object(video, "VIDEO_OUTPUT_ROOT", self.root):
            self.assertEqual(self.runtime.output_file("fixture-job").name, "fixture-job.mp4")

    def test_cancelled_job_cannot_become_complete(self):
        request = self.run_fixture(cancelled=True)
        self.assertEqual(self.runtime.phase, "cancelled")
        self.assertIsNone(self.runtime.output_path)
        self.assertTrue(any("/queue" in str(call) for call in request.call_args_list))

    def test_backend_failure_is_not_completion(self):
        self.run_fixture(failed=True)
        self.assertEqual(self.runtime.phase, "failed")
        self.assertIn("fixture backend failure", self.runtime.error)


if __name__ == "__main__":
    unittest.main()
