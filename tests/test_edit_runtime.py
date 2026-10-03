from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.edit_runtime import EditRuntime, decode_reference, pipeline_directory, offline_environment
from runtime.edit_worker import PRESETS, PRESERVE
from runtime.package_runtime import package_cache_key

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBf8AAAAASUVORK5CYII=")
REFERENCE = "data:image/png;base64," + base64.b64encode(PNG).decode()


class PackageFixture:
    def __init__(self, directory: Path, nested: bool = True) -> None:
        self.root = directory / "packages"
        self.path = "image_edit/Qwen-Image-Edit-2511"
        self.destination = self.root / package_cache_key(self.path)
        self.model = self.destination / "model" if nested else self.destination
        self.model.mkdir(parents=True)
        (self.model / "model_index.json").write_text(json.dumps({"_class_name": "QwenImageEditPlusPipeline"}))
        for component in ("transformer", "text_encoder", "vae", "tokenizer", "scheduler", "processor"):
            target = self.model / component
            target.mkdir()
            name = {"tokenizer":"tokenizer_config.json", "scheduler":"scheduler_config.json", "processor":"preprocessor_config.json"}.get(component, "config.json")
            (target / name).write_text("{}")
            if component in ("transformer", "text_encoder", "vae"):
                (target / "model.safetensors").write_bytes(b"fixture-only-not-real-weights")
        self.state = {"phase":"complete", "package_path":self.path, "package_dir":str(self.destination), "running":False}
        self.write_manifest()

    def write_manifest(self) -> None:
        files = [{"relative_path": path.relative_to(self.destination).as_posix(), "size": path.stat().st_size}
                 for path in self.destination.rglob("*") if path.is_file() and path.name != ".jvust-package.json"]
        (self.destination / ".jvust-package.json").write_text(json.dumps({"schema_version": 1, "package_path": self.path, "files": files}))

    def snapshot(self) -> dict:
        return self.state.copy()


class ReferenceTests(unittest.TestCase):
    def test_png(self) -> None:
        self.assertEqual(decode_reference(REFERENCE), (PNG, ".png"))

    def test_jpeg_and_webp(self) -> None:
        jpeg = b"\xff\xd8\xff\xc0\0\x0b\x08\0\x01\0\x01\x01\x01\x11\0\xff\xd9"
        webp = b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + bytes(10)
        for mime, data, suffix in [("jpeg", jpeg, ".jpg"), ("webp", webp, ".webp")]:
            self.assertEqual(decode_reference("data:image/"+mime+";base64,"+base64.b64encode(data).decode()), (data,suffix))

    def test_rejects_remote_paths_html_and_bad_base64(self) -> None:
        for value in (None, "https://example.com/a.png", "C:/private.png", "data:image/svg+xml;base64,abc", "data:image/png;base64,!!!!", "data:image/png;base64,YWJj"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_reference(value)

    def test_rejects_size(self) -> None:
        with patch("runtime.edit_runtime.MAX_IMAGE_BYTES", 10), self.assertRaises(ValueError):
            decode_reference(REFERENCE)

    def test_offline_credentials_not_inherited(self) -> None:
        with patch.dict(os.environ, {"DRIVE_TOKEN":"private", "OPENAI_API_KEY":"private", "MODEL_REMOTE_TOKEN":"private"}):
            env = offline_environment()
            self.assertNotIn("DRIVE_TOKEN", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("MODEL_REMOTE_TOKEN", env)
            self.assertEqual(env["HF_HUB_OFFLINE"], "1")


class PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.package = PackageFixture(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_nested_package(self) -> None:
        self.assertEqual(pipeline_directory(self.package.root, self.package.snapshot()), self.package.model.resolve())

    def test_flat_package(self) -> None:
        flat = PackageFixture(Path(self.tmp.name)/"flat", nested=False)
        self.assertEqual(pipeline_directory(flat.root, flat.snapshot()), flat.model.resolve())

    def test_requires_complete_state(self) -> None:
        for phase in ("starting", "downloading", "failed", "cancelled"):
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                pipeline_directory(self.package.root, {**self.package.snapshot(), "phase":phase})

    def test_arbitrary_directory_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pipeline_directory(self.package.root, {**self.package.snapshot(), "package_dir":self.tmp.name})

    def test_missing_config_rejected(self) -> None:
        (self.package.model / "processor/preprocessor_config.json").unlink()
        with self.assertRaisesRegex(ValueError, "processor"):
            pipeline_directory(self.package.root, self.package.snapshot())

    def test_wrong_pipeline_rejected(self) -> None:
        (self.package.model / "model_index.json").write_text('{"_class_name":"OtherPipeline"}')
        self.package.write_manifest()
        with self.assertRaisesRegex(ValueError, "QwenImageEditPlusPipeline"):
            pipeline_directory(self.package.root, self.package.snapshot())

    def test_missing_weight_shard_rejected(self) -> None:
        (self.package.model / "transformer/model.safetensors.index.json").write_text(json.dumps({"weight_map":{"x":"missing.safetensors"}}))
        self.package.write_manifest()
        with self.assertRaisesRegex(ValueError, "missing.safetensors"):
            pipeline_directory(self.package.root, self.package.snapshot())

    def test_shard_traversal_rejected(self) -> None:
        (self.package.model / "transformer/model.safetensors.index.json").write_text(json.dumps({"weight_map":{"x":"../../../../outside.safetensors"}}))
        self.package.write_manifest()
        with self.assertRaises(ValueError):
            pipeline_directory(self.package.root, self.package.snapshot())

    def test_empty_weights_rejected(self) -> None:
        (self.package.model / "vae/model.safetensors").write_bytes(b"")
        with self.assertRaisesRegex(ValueError,"vae"):
            pipeline_directory(self.package.root, self.package.snapshot())


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.package = PackageFixture(Path(self.tmp.name))
        self.runtime = EditRuntime(self.package)
        self.worker = Path(self.tmp.name) / "fake_worker.py"
        self.worker.write_text("import sys,time\nfrom pathlib import Path\nprint('generating: fixture',flush=True)\nPath(sys.argv[sys.argv.index('--output')+1]).write_bytes("+repr(PNG)+")\n")
        self.worker_patch = patch("runtime.edit_runtime.worker_path", return_value=self.worker)
        self.worker_patch.start()
        self.python_patch = patch("runtime.edit_runtime.python_path", return_value=sys.executable)
        self.python_patch.start()
        self.payload = {"model_id":"qwen_image_edit_2511", "package_path":self.package.path,
                        "operation":"soft-lighting", "reference_image":REFERENCE, "seed":7}

    def tearDown(self) -> None:
        self.runtime.stop()
        self.worker_patch.stop()
        self.python_patch.stop()
        self.tmp.cleanup()

    def wait_finished(self) -> dict:
        deadline = time.monotonic() + 8
        while self.runtime.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(.01)
        return self.runtime.snapshot()

    def test_actual_subprocess_lifecycle(self) -> None:
        start = self.runtime.start(self.payload)
        state = self.wait_finished()
        self.assertEqual(state["phase"], "complete")
        self.assertTrue(state["output_ready"])
        self.assertEqual(self.runtime.output_file(start["job_id"]).read_bytes(), PNG)
        with self.assertRaises(ValueError):
            self.runtime.output_file("../../private")

    def test_worker_nonzero_not_reported_complete(self) -> None:
        self.worker.write_text("raise SystemExit(7)\n")
        self.runtime.start(self.payload)
        state = self.wait_finished()
        self.assertEqual(state["phase"], "failed")
        self.assertFalse(state["output_ready"])
        self.assertIn("7", state["error"])

    def test_worker_missing_output_not_reported_complete(self) -> None:
        self.worker.write_text("print('no output')\n")
        self.runtime.start(self.payload)
        self.assertEqual(self.wait_finished()["phase"], "failed")

    def test_cancel_terminates_process(self) -> None:
        self.worker.write_text("import time\nprint('generating: waiting',flush=True)\ntime.sleep(30)\n")
        self.runtime.start(self.payload)
        deadline = time.monotonic() + 3
        while not self.runtime.process and time.monotonic() < deadline:
            time.sleep(.01)
        self.runtime.stop()
        state = self.wait_finished()
        self.assertEqual(state["phase"], "cancelled")
        self.assertFalse(state["output_ready"])

    def test_busy_rejected(self) -> None:
        self.worker.write_text("import time\ntime.sleep(30)\n")
        self.runtime.start(self.payload)
        with self.assertRaises(RuntimeError):
            self.runtime.start(self.payload)

    def test_arbitrary_prompts_and_operations_rejected(self) -> None:
        for value in ({"prompt":"arbitrary"}, {"instruction":"arbitrary"}, {"operation":"unsupported"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runtime.start({**self.payload, **value})

    def test_model_and_package_mismatch_rejected(self) -> None:
        for value in ({"model_id":"other"}, {"package_path":"other"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runtime.start({**self.payload, **value})

    def test_parameter_bounds(self) -> None:
        for value in ({"steps":0}, {"steps":51}, {"seed":-1}, {"seed":2**32}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runtime.start({**self.payload, **value})

    def test_environment_failure_is_not_ready(self) -> None:
        self.worker.write_text('import json\nprint(json.dumps({"supported":True}))\nraise SystemExit(5)\n')
        self.assertFalse(self.runtime.environment()["supported"])

    def test_presets_preserve_subject_and_clothing(self) -> None:
        self.assertEqual(len(PRESETS),3)
        for prompt in PRESETS.values():
            self.assertTrue(prompt.startswith(PRESERVE))
            self.assertIn("clothing", prompt)
