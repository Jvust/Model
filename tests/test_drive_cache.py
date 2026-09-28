import io
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime.drive_cache import DriveCache, DriveFileSpec, cache_key, validate_drive_file_id


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = io.BytesIO(body)
        self.status = status

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DriveCacheTests(unittest.TestCase):
    def test_rejects_invalid_drive_file_id(self):
        with self.assertRaises(ValueError):
            validate_drive_file_id("../bad")

    def test_cache_key_does_not_expose_file_id(self):
        file_id = "1AbCdEfGhIjKlMnOp"
        self.assertNotIn(file_id, cache_key(file_id))

    def test_downloads_and_reuses_cached_file_without_storing_token(self):
        body = b"GGUF" + b"x" * 20
        with tempfile.TemporaryDirectory() as temp:
            cache = DriveCache(Path(temp))
            spec = DriveFileSpec(
                file_id="1AbCdEfGhIjKlMnOp",
                name="model.gguf",
                size=len(body),
                md5_checksum=hashlib.md5(body).hexdigest(),
                resource_key="rk",
            )

            with mock.patch(
                "runtime.drive_cache.urlopen",
                return_value=FakeResponse(body, 200),
            ) as opener:
                path = cache.download(spec, "secret-access-token")

            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), body)
            self.assertEqual(opener.call_count, 1)

            metadata_files = list(Path(temp).glob("*.json"))
            self.assertEqual(len(metadata_files), 1)
            metadata_text = metadata_files[0].read_text(encoding="utf-8")
            self.assertNotIn("secret-access-token", metadata_text)
            data = json.loads(metadata_text)
            self.assertEqual(data["file_id"], spec.file_id)

            with mock.patch("runtime.drive_cache.urlopen") as opener2:
                cached = cache.download(spec, "new-token")
            self.assertEqual(cached, path)
            opener2.assert_not_called()

    def test_cache_entries_persist_until_explicit_delete(self):
        body = b"GGUF" + b"x" * 20
        with tempfile.TemporaryDirectory() as temp:
            cache = DriveCache(Path(temp))
            spec = DriveFileSpec(
                file_id="1AbCdEfGhIjKlMnOp",
                name="persistent.gguf",
                size=len(body),
            )
            with mock.patch(
                "runtime.drive_cache.urlopen",
                return_value=FakeResponse(body, 200),
            ):
                cache.download(spec, "token")

            entries = cache.list_entries()
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]["cached"])
            self.assertEqual(entries[0]["name"], "persistent.gguf")

            removed = cache.delete_file_id(spec.file_id)
            self.assertTrue(removed["removed"])
            self.assertEqual(cache.list_entries(), [])

    def test_download_preflight_reports_cache_space(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = DriveCache(Path(temp))
            spec = DriveFileSpec(
                file_id="1AbCdEfGhIjKlMnOp",
                name="preflight.gguf",
                size=1024,
            )
            status = cache.download_preflight(spec, reserve_bytes=0)
            self.assertTrue(status["ok"])
            self.assertGreater(status["disk"]["free_bytes"], 0)
            self.assertEqual(status["remaining_bytes"], 1024)

    def test_payload_uses_file_name_for_extension(self):
        spec = DriveFileSpec.from_payload(
            {
                "drive_file_id": "1AbCdEfGhIjKlMnOp",
                "file_name": "qwen.gguf",
                "name": "Qwen Display Name",
                "size": 24,
            }
        )
        self.assertEqual(spec.name, "qwen.gguf")
        self.assertEqual(spec.size, 24)


if __name__ == "__main__":
    unittest.main()
