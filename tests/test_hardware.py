import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.hardware import (
    _parse_cuda_version,
    _parse_query_line,
    disk_status,
    gguf_preflight,
    system_memory_status,
)


class NvidiaParserTests(unittest.TestCase):
    def test_parses_query_line(self):
        info = _parse_query_line("NVIDIA RTX Test, 12288 MiB, 555.42")
        self.assertEqual(info["gpu_name"], "NVIDIA RTX Test")
        self.assertEqual(info["vram_total_mb"], 12288)
        self.assertEqual(info["driver_version"], "555.42")

    def test_parses_cuda_version(self):
        self.assertEqual(
            _parse_cuda_version("| NVIDIA-SMI 555 | CUDA Version: 12.6 |"),
            "12.6",
        )


class HardwareStatusTests(unittest.TestCase):
    def test_disk_status_has_free_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            status = disk_status(Path(temp))
            self.assertGreater(status["free_bytes"], 0)
            self.assertGreater(status["total_bytes"], 0)

    def test_system_memory_status_shape(self):
        status = system_memory_status()
        self.assertIn("available_bytes", status)
        self.assertIn("total_bytes", status)

    def test_gguf_preflight_accepts_small_model(self):
        # This is a success-path unit test, not a live host RAM requirement.
        with tempfile.TemporaryDirectory() as temp, patch(
            "runtime.hardware.system_memory_status",
            return_value={"available_bytes": 8 * 1024**3, "total_bytes": 16 * 1024**3},
        ):
            status = gguf_preflight(
                Path(temp),
                expected_bytes=1024,
                partial_bytes=0,
                threads=2,
                reserve_bytes=0,
            )
            self.assertTrue(status["ok"])
            self.assertEqual(status["threads"], 2)


if __name__ == "__main__":
    unittest.main()
