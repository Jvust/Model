import struct
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import runtime.local_bridge as bridge


class DriveSessionTests(unittest.TestCase):
    def test_token_is_memory_only_state(self):
        session = bridge.DriveSession()
        session.set("token-value")
        self.assertEqual(session.get(), "token-value")
        session.clear()
        with self.assertRaises(PermissionError):
            session.get()

    def test_rejects_empty_token(self):
        session = bridge.DriveSession()
        with self.assertRaises(ValueError):
            session.set("")


class RemoteTokenTests(unittest.TestCase):
    def test_remote_token_disabled_accepts_request(self):
        self.assertTrue(bridge.remote_token_valid("", None, None))

    def test_accepts_bearer_token(self):
        self.assertTrue(
            bridge.remote_token_valid(
                "secret-token",
                "Bearer secret-token",
                None,
            )
        )

    def test_accepts_legacy_header_token(self):
        self.assertTrue(
            bridge.remote_token_valid(
                "secret-token",
                None,
                "secret-token",
            )
        )

    def test_rejects_missing_or_wrong_token(self):
        self.assertFalse(
            bridge.remote_token_valid("secret-token", None, None)
        )
        self.assertFalse(
            bridge.remote_token_valid(
                "secret-token",
                "Bearer wrong-token",
                None,
            )
        )


class MediaTicketTests(unittest.TestCase):
    def test_media_ticket_round_trip(self):
        secret = "remote-secret"
        expires = 2800
        ticket = bridge.media_ticket_signature(
            secret,
            "video",
            "job-123",
            expires,
        )
        self.assertTrue(
            bridge.media_ticket_valid(
                secret,
                "video",
                "job-123",
                expires,
                ticket,
                now=1000,
            )
        )

    def test_media_ticket_is_bound_to_kind_and_job(self):
        secret = "remote-secret"
        expires = 2800
        ticket = bridge.media_ticket_signature(
            secret,
            "image",
            "job-123",
            expires,
        )
        self.assertFalse(
            bridge.media_ticket_valid(
                secret,
                "video",
                "job-123",
                expires,
                ticket,
                now=1000,
            )
        )
        self.assertFalse(
            bridge.media_ticket_valid(
                secret,
                "image",
                "job-other",
                expires,
                ticket,
                now=1000,
            )
        )

    def test_media_ticket_rejects_expired_and_excessive_expiry(self):
        secret = "remote-secret"
        expired = 999
        expired_ticket = bridge.media_ticket_signature(
            secret,
            "image",
            "job-123",
            expired,
        )
        self.assertFalse(
            bridge.media_ticket_valid(
                secret,
                "image",
                "job-123",
                expired,
                expired_ticket,
                now=1000,
            )
        )

        far_future = 1000 + bridge.MEDIA_TICKET_TTL_SECONDS + 120
        future_ticket = bridge.media_ticket_signature(
            secret,
            "image",
            "job-123",
            far_future,
        )
        self.assertFalse(
            bridge.media_ticket_valid(
                secret,
                "image",
                "job-123",
                far_future,
                future_ticket,
                now=1000,
            )
        )

    def test_media_status_signs_only_remote_ready_output(self):
        snapshot = {
            "job_id": "job-123",
            "output_ready": True,
            "phase": "complete",
        }
        with mock.patch.object(bridge, "REMOTE_TOKEN", "remote-secret"):
            signed = bridge.media_status("image", snapshot, now=1000)
        self.assertEqual(
            signed["media_expires"],
            1000 + bridge.MEDIA_TICKET_TTL_SECONDS,
        )
        self.assertTrue(
            bridge.media_ticket_valid(
                "remote-secret",
                "image",
                "job-123",
                signed["media_expires"],
                signed["media_ticket"],
                now=1000,
            )
        )

        with mock.patch.object(bridge, "REMOTE_TOKEN", ""):
            local = bridge.media_status("image", snapshot, now=1000)
        self.assertNotIn("media_ticket", local)
        self.assertNotIn("media_expires", local)


class GgufInspectionTests(unittest.TestCase):
    def test_reads_fixed_header_without_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "tiny.gguf"
            model.write_bytes(struct.pack("<4sIQQ", b"GGUF", 3, 42, 17) + b"payload")
            info = bridge.inspect_gguf(model)
            self.assertEqual(info["version"], 3)
            self.assertEqual(info["tensor_count"], 42)
            self.assertEqual(info["metadata_kv_count"], 17)
            self.assertEqual(info["header_bytes_read"], 24)
            self.assertEqual(info["file_size"], 31)

    def test_rejects_wrong_magic(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "fake.gguf"
            model.write_bytes(struct.pack("<4sIQQ", b"NOPE", 3, 1, 1))
            with self.assertRaises(ValueError):
                bridge.inspect_gguf(model)

    def test_rejects_truncated_header(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "short.gguf"
            model.write_bytes(b"GGUF")
            with self.assertRaises(ValueError):
                bridge.inspect_gguf(model)


class LoadModeTests(unittest.TestCase):
    def test_accepts_current_llama_load_modes(self):
        for mode in ("auto", "none", "mmap", "mlock", "mmap+mlock", "dio"):
            self.assertEqual(bridge.normalize_load_mode(mode), mode)

    def test_normalizes_case_and_whitespace(self):
        self.assertEqual(bridge.normalize_load_mode("  MMAP  "), "mmap")

    def test_rejects_unknown_load_mode(self):
        with self.assertRaises(ValueError):
            bridge.normalize_load_mode("legacy")


class ChatPayloadTests(unittest.TestCase):
    def test_builds_safe_non_streaming_payload(self):
        payload = bridge.build_chat_payload(
            {"messages": [{"role": "user", "content": "你好"}]},
            "qwen.gguf",
        )
        self.assertEqual(payload["model"], "qwen.gguf")
        self.assertEqual(payload["messages"][0]["content"], "你好")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["max_tokens"], 512)

    def test_rejects_unknown_role(self):
        with self.assertRaises(ValueError):
            bridge.build_chat_payload(
                {"messages": [{"role": "tool", "content": "x"}]},
                "model.gguf",
            )

    def test_rejects_invalid_temperature(self):
        with self.assertRaises(ValueError):
            bridge.build_chat_payload(
                {"messages": [{"role": "user", "content": "x"}], "temperature": 3},
                "model.gguf",
            )

    def test_rejects_invalid_max_tokens(self):
        with self.assertRaises(ValueError):
            bridge.build_chat_payload(
                {"messages": [{"role": "user", "content": "x"}], "max_tokens": 0},
                "model.gguf",
            )


if __name__ == "__main__":
    unittest.main()
