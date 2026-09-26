import struct
import tempfile
import unittest
from pathlib import Path

from runtime.drive_cache import DriveCache
from runtime.task_runtime import (
    EMBEDDING_BYTES,
    EMBEDDING_FILE,
    RERANKER_BYTES,
    RERANKER_FILE,
    TASK_ADAPTERS,
    TaskRuntime,
    adapter_for,
    build_task_command,
    inspect_gguf,
    task_file_spec,
)


class TaskAdapterTests(unittest.TestCase):
    def test_matches_embedding(self):
        matched = adapter_for(
            "Qwen3-Embedding-0.6B",
            "qwen3_embedding_0_6b",
            "rag/Qwen__Qwen3-Embedding-0.6B-GGUF",
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched[0], "qwen3_embedding_0_6b")

    def test_matches_reranker(self):
        matched = adapter_for(
            "Qwen3-Reranker-0.6B",
            "qwen3_reranker_0_6b",
            "rag/ggml-org__Qwen3-Reranker-0.6B-Q8_0-GGUF",
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched[0], "qwen3_reranker_0_6b")

    def test_embedding_and_reranker_flags_stay_separate(self):
        embedding = build_task_command(
            "llama-server",
            Path("embedding.gguf"),
            TASK_ADAPTERS["qwen3_embedding_0_6b"],
        )
        reranker = build_task_command(
            "llama-server",
            Path("reranker.gguf"),
            TASK_ADAPTERS["qwen3_reranker_0_6b"],
        )
        self.assertIn("--embedding", embedding)
        self.assertIn("last", embedding)
        self.assertNotIn("--rerank", embedding)
        self.assertNotIn("--reranking", embedding)
        self.assertIn("--embedding", reranker)
        self.assertTrue("--rerank" in reranker or "--reranking" in reranker)
        self.assertIn("rank", reranker)
        self.assertIn("--ubatch-size", embedding)
        self.assertIn("4096", embedding)

    def test_embedding_requires_exact_file(self):
        spec = task_file_spec(
            {
                "files": [
                    {
                        "id": "testEmbeddingFile123456",
                        "name": EMBEDDING_FILE,
                        "size": EMBEDDING_BYTES,
                    }
                ]
            },
            TASK_ADAPTERS["qwen3_embedding_0_6b"],
        )
        self.assertEqual(spec.name, EMBEDDING_FILE)

    def test_reranker_rejects_wrong_size(self):
        with self.assertRaises(ValueError):
            task_file_spec(
                {
                    "files": [
                        {
                            "id": "testRerankerFile123456",
                            "name": RERANKER_FILE,
                            "size": RERANKER_BYTES - 1,
                        }
                    ]
                },
                TASK_ADAPTERS["qwen3_reranker_0_6b"],
            )

    def test_gguf_header(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.gguf"
            path.write_bytes(struct.pack("<4sIQQ", b"GGUF", 3, 7, 11))
            info = inspect_gguf(path)
            self.assertEqual(info["version"], 3)
            self.assertEqual(info["tensor_count"], 7)


class TaskPayloadTests(unittest.TestCase):
    def test_embedding_payload_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = TaskRuntime(
                DriveCache(Path(temp)),
                lambda: "token",
                lambda: "llama-server",
            )
            runtime.phase = "ready"
            runtime.kind = "embedding"
            with self.assertRaises(ValueError):
                runtime.embeddings({"input": []})

    def test_rerank_payload_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = TaskRuntime(
                DriveCache(Path(temp)),
                lambda: "token",
                lambda: "llama-server",
            )
            runtime.phase = "ready"
            runtime.kind = "reranker"
            with self.assertRaises(ValueError):
                runtime.rerank({"query": "", "documents": ["a"]})


if __name__ == "__main__":
    unittest.main()
