import tempfile
import unittest
from pathlib import Path

from runtime.drive_cache import DriveCache
from runtime.package_runtime import (
    PackageRuntime,
    manifest_summary,
    normalize_manifest,
    package_cache_key,
)


def file(file_id, name, relative_path, size):
    return {
        "drive_file_id": file_id,
        "file_name": name,
        "relative_path": relative_path,
        "size": size,
    }


class PackageManifestTests(unittest.TestCase):
    def test_normalizes_paths_relative_to_package(self):
        package_path, files = normalize_manifest(
            {
                "package_path": "video_ultra/Wan2.2-T2V-A14B",
                "manifest_files": [
                    file(
                        "driveFile12345678",
                        "config.json",
                        "video_ultra/Wan2.2-T2V-A14B/config.json",
                        250,
                    ),
                    file(
                        "driveFileABCDEFGH",
                        "weights.safetensors",
                        "video_ultra/Wan2.2-T2V-A14B/high_noise_model/weights.safetensors",
                        1000,
                    ),
                ],
            }
        )
        self.assertEqual(package_path, "video_ultra/Wan2.2-T2V-A14B")
        self.assertEqual(files[0]["relative_path"], "config.json")
        self.assertEqual(
            files[1]["relative_path"],
            "high_noise_model/weights.safetensors",
        )

    def test_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            normalize_manifest(
                {
                    "package_path": "video_ultra/Wan2.2-T2V-A14B",
                    "manifest_files": [
                        file(
                            "driveFile12345678",
                            "config.json",
                            "../config.json",
                            250,
                        )
                    ],
                }
            )

    def test_rejects_duplicate_drive_ids(self):
        with self.assertRaises(ValueError):
            normalize_manifest(
                {
                    "package_path": "video_ultra/Wan2.2-T2V-A14B",
                    "manifest_files": [
                        file(
                            "driveFile12345678",
                            "a.json",
                            "video_ultra/Wan2.2-T2V-A14B/a.json",
                            100,
                        ),
                        file(
                            "driveFile12345678",
                            "b.json",
                            "video_ultra/Wan2.2-T2V-A14B/b.json",
                            100,
                        ),
                    ],
                }
            )

    def test_summary_separates_weights_and_support_files(self):
        summary = manifest_summary(
            {
                "package_path": "video_ultra/Wan2.2-T2V-A14B",
                "manifest_files": [
                    file(
                        "driveFile12345678",
                        "config.json",
                        "video_ultra/Wan2.2-T2V-A14B/config.json",
                        250,
                    ),
                    file(
                        "driveFileABCDEFGH",
                        "weights.safetensors",
                        "video_ultra/Wan2.2-T2V-A14B/weights.safetensors",
                        1000,
                    ),
                ],
            }
        )
        self.assertEqual(summary["file_count"], 2)
        self.assertEqual(summary["model_file_count"], 1)
        self.assertEqual(summary["support_file_count"], 1)
        self.assertEqual(summary["total_bytes"], 1250)

    def test_package_cache_key_is_stable(self):
        first = package_cache_key("video_ultra/Wan2.2-T2V-A14B")
        second = package_cache_key("video_ultra/Wan2.2-T2V-A14B")
        self.assertEqual(first, second)
        self.assertIn("Wan2.2-T2V-A14B", first)


class PackageRuntimeTests(unittest.TestCase):
    def test_snapshot_starts_idle(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = DriveCache(Path(temp) / "cache")
            runtime = PackageRuntime(cache, lambda: "token")
            self.assertEqual(runtime.snapshot()["phase"], "idle")
            self.assertFalse(runtime.snapshot()["running"])


if __name__ == "__main__":
    unittest.main()
