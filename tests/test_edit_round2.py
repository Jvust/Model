"""State, provenance and worker failure regressions; never loads real GPU weights."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.edit_runtime import EditRuntime, decode_reference, pipeline_directory
from test_edit_runtime import PackageFixture, PNG, REFERENCE


class EditRound2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.package = PackageFixture(Path(self.tmp.name))
        self.runtime = EditRuntime(self.package)
        self.addCleanup(self.runtime.stop)
        self.worker = Path(self.tmp.name) / "fixture_worker.py"
        self.worker.write_text("import sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index('--output')+1]).write_bytes(" + repr(PNG) + ")\n")
        self.worker_patch = patch("runtime.edit_runtime.worker_path", return_value=self.worker)
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.python_patch = patch("runtime.edit_runtime.python_path", return_value=sys.executable)
        self.python_patch.start()
        self.addCleanup(self.python_patch.stop)
        self.payload = {"model_id": "qwen_image_edit_2511", "package_path": self.package.path,
                        "operation": "soft-lighting", "reference_image": REFERENCE, "seed": 0}

    def finished(self) -> dict:
        self.runtime.thread.join(timeout=5)
        self.assertFalse(self.runtime.snapshot()["running"])
        return self.runtime.snapshot()

    def test_rejects_truncated_and_oversized_containers(self) -> None:
        huge = bytearray(PNG)
        huge[16:20] = (12000001).to_bytes(4, "big")
        for data in (PNG[:24], b"\x89PNG\r\n\x1a\n", bytes(huge)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                decode_reference("data:image/png;base64," + base64.b64encode(data).decode())

    def test_rejects_signature_only_jpeg_webp(self) -> None:
        for mime, data in (("jpeg", b"\xff\xd8\xfffixture"), ("webp", b"RIFF0000WEBPfixture")):
            with self.subTest(mime=mime), self.assertRaises(ValueError):
                decode_reference("data:image/" + mime + ";base64," + base64.b64encode(data).decode())

    def test_manifest_must_identify_package_and_schema(self) -> None:
        manifest = self.package.destination / ".jvust-package.json"
        original = json.loads(manifest.read_text())
        for change in ({}, {**original, "package_path": "other"}, {**original, "schema_version": 9}, {**original, "files": []}):
            manifest.write_text(json.dumps(change))
            with self.subTest(change=change), self.assertRaises(ValueError):
                pipeline_directory(self.package.root, self.package.snapshot())

    def test_manifest_rejects_mismatch_duplicate_and_traversal(self) -> None:
        manifest = self.package.destination / ".jvust-package.json"
        original = json.loads(manifest.read_text())
        for entries in ([{**original["files"][0], "size": 0}], [original["files"][0]] * 2,
                        [{"relative_path": "../outside.json", "size": 0}], [{"relative_path": "C:/outside.json", "size": 0}]):
            manifest.write_text(json.dumps({**original, "files": entries}))
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                pipeline_directory(self.package.root, self.package.snapshot())

    def test_loader_files_must_be_in_completed_manifest(self) -> None:
        (self.package.model / "tokenizer/added_tokens.json").write_text("{}")
        with self.assertRaises(ValueError):
            pipeline_directory(self.package.root, self.package.snapshot())

    def test_malformed_shard_value_is_validation_error(self) -> None:
        path = self.package.model / "transformer/model.safetensors.index.json"
        for value in (["bad"], "../vae/model.safetensors", "config.json"):
            path.write_text(json.dumps({"weight_map": {"x": value}}))
            self.package.write_manifest()
            with self.subTest(value=value), self.assertRaises(ValueError):
                pipeline_directory(self.package.root, self.package.snapshot())

    def test_rejects_other_package_even_with_pipeline_class(self) -> None:
        with self.assertRaises(ValueError):
            pipeline_directory(self.package.root, {**self.package.snapshot(), "package_path": "models/other"})

    def test_environment_invalid_json_shape_and_truthy_values(self) -> None:
        for value in ([], None, "yes", {"supported": "yes"}, {"supported": 1}):
            self.worker.write_text("print(" + repr(json.dumps(value)) + ")\n")
            with self.subTest(value=value):
                self.assertIs(self.runtime.environment()["supported"], False)

    def test_environment_timeout_is_explicitly_not_ready(self) -> None:
        with patch("runtime.edit_runtime.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 45)):
            state = self.runtime.environment()
        self.assertFalse(state["supported"])
        self.assertIsNone(state["exit_status"])

    def test_invalid_payload_and_integer_types_are_rejected(self) -> None:
        for payload in (None, [], {**self.payload, "seed": True}, {**self.payload, "steps": 1.5}, {**self.payload, "seed": ""}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.runtime.start(payload)

    def test_snapshot_includes_ownership_and_observed_exit(self) -> None:
        started = self.runtime.start(self.payload)
        state = self.finished()
        self.assertEqual(state["package_path"], self.package.path)
        self.assertEqual(state["operation"], "soft-lighting")
        self.assertEqual(state["exit_status"], 0)
        self.assertEqual(state["job_id"], started["job_id"])
        self.assertEqual(list(self.runtime.root.glob("*-input.*")), [])

    def test_invalid_output_is_failed_and_removed(self) -> None:
        for data in (b"not a PNG", PNG[:33]):
            self.worker.write_text("import sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index('--output')+1]).write_bytes(" + repr(data) + ")\n")
            self.runtime.start(self.payload)
            state = self.finished()
            self.assertEqual(state["phase"], "failed")
            self.assertFalse(state["output_ready"])
            self.assertFalse(self.runtime.output.exists())

    def test_stale_stop_cannot_cancel_new_job(self) -> None:
        first = self.runtime.start(self.payload)
        self.finished()
        self.worker.write_text("import time\ntime.sleep(30)\n")
        second = self.runtime.start(self.payload)
        with self.assertRaises(ValueError):
            self.runtime.stop(first["job_id"])
        self.assertTrue(self.runtime.snapshot()["running"])
        self.runtime.stop(second["job_id"])
        self.assertEqual(self.finished()["phase"], "cancelled")

    def test_cancel_waits_for_process_and_removes_input(self) -> None:
        self.worker.write_text("import time\nprint('generating: fixture',flush=True)\ntime.sleep(30)\n")
        job = self.runtime.start(self.payload)
        deadline = time.monotonic() + 3
        while not self.runtime.process and time.monotonic() < deadline:
            time.sleep(.01)
        process = self.runtime.process
        state = self.runtime.stop(job["job_id"])
        self.assertFalse(state["running"])
        self.assertIsNotNone(process.poll())
        self.assertEqual(list(self.runtime.root.glob("*-input.*")), [])

    def test_worker_cli_bounds_fail_before_dependencies(self) -> None:
        from runtime import edit_worker
        for args in (("--seed", "-1"), ("--steps", "51")):
            result = subprocess.run([sys.executable, "-I", edit_worker.__file__, *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("seed must be", result.stderr)


if __name__ == "__main__":
    unittest.main()
