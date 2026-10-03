import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from runtime.package_runtime import PackageRuntime, normalize_manifest
from runtime.local_bridge import Handler


def payload(paths=("config.json",)):
    return {"package_path": "image_edit/fixture", "manifest_files": [
        {"relative_path": path, "drive_file_id": f"fixture-file-{index}",
         "file_name": path.split("/")[-1], "size": 2}
        for index, path in enumerate(paths)]}


class PackageRound2Tests(unittest.TestCase):
    def test_rejects_absolute_windows_and_reserved_paths(self):
        for name in ("/config.json", "C:/config.json", "CON.json", "dir/a. ", ".jvust-package.json", "a/../b"):
            with self.subTest(path=name), self.assertRaises(ValueError):
                normalize_manifest(payload((name,)))

    def test_rejects_case_collisions_and_file_parent_conflicts(self):
        for paths in (("a.json", "A.json"), ("a", "a/b.json")):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                normalize_manifest(payload(paths))

    def test_stop_is_cancelling_while_worker_is_alive(self):
        with tempfile.TemporaryDirectory() as tmp, patch("runtime.package_runtime.PACKAGE_ROOT", Path(tmp)):
            runtime = PackageRuntime(Mock(), lambda: "fixture")
            runtime.thread = Mock(is_alive=Mock(return_value=True))
            runtime.phase = "downloading"
            result = runtime.stop()
            self.assertEqual(result["phase"], "cancelling")
            self.assertTrue(result["running"])

    def test_cancel_during_final_download_cannot_publish_complete_manifest(self):
        with tempfile.TemporaryDirectory() as tmp, patch("runtime.package_runtime.PACKAGE_ROOT", Path(tmp)):
            cache = Mock()
            cache.cached_path.return_value = None
            runtime = PackageRuntime(cache, lambda: "fixture")
            cached = Path(tmp) / "cached.json"
            cached.write_bytes(b"{}")
            def download(*args):
                runtime.cancel.set()
                return cached
            cache.download.side_effect = download
            package_path, files = normalize_manifest(payload())
            destination = Path(tmp) / "package"
            runtime.total_bytes = 2
            runtime._run(package_path, files, "fixture", destination)
            self.assertEqual(runtime.snapshot()["phase"], "cancelled")
            self.assertFalse((destination / ".jvust-package.json").exists())


class MediaRangeRound2Tests(unittest.TestCase):
    def serve(self, value, data=b"0123456789"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.mp4"
            path.write_bytes(data)
            handler = object.__new__(Handler)
            handler.headers = {"Range": value} if value else {}
            handler.wfile = io.BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._cors_headers = Mock()
            handler._serve_video_file(path)
            return handler.send_response.call_args.args[0], dict(call.args for call in handler.send_header.call_args_list), handler.wfile.getvalue()

    def test_suffix_range_returns_last_bytes(self):
        status, headers, data = self.serve("bytes=-4")
        self.assertEqual((status, data, headers["Content-Range"]), (206, b"6789", "bytes 6-9/10"))

    def test_open_range_and_clamped_end(self):
        for value in ("bytes=7-", "bytes=7-99"):
            self.assertEqual(self.serve(value)[2], b"789")

    def test_invalid_range_returns_416(self):
        for value in ("bytes=-0", "bytes=wrong", "bytes=10-", "bytes=9-2"):
            status, headers, data = self.serve(value)
            self.assertEqual((status, data, headers["Content-Range"]), (416, b"", "bytes */10"))

    def test_unsupported_multiple_range_is_ignored_not_misrepresented(self):
        self.assertEqual(self.serve("bytes=0-1,8-9")[0], 200)


if __name__ == "__main__":
    unittest.main()
