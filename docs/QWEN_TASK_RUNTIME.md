# Qwen3 Embedding / Reranker task runtime

## Goal

Enable the first two Stage 4 task models without introducing a second Python/Transformers runtime into the Windows package.

The implementation reuses the existing user-provided `llama-server.exe`, the Drive API cache, and the persistent `D:\Model` cache, but runs task models on a separate localhost port so chat is not replaced.

## Drive bootstrap

Drive artifact:

`AI-Model-Vault/notebook_launchers/启动_Qwen3_Embedding_Reranker_0.6B_Q8_0_DriveFirst.ipynb`

Targets:

### Embedding

- source: `Qwen/Qwen3-Embedding-0.6B-GGUF`
- file: `Qwen3-Embedding-0.6B-Q8_0.gguf`
- exact bytes: 639,150,592
- SHA256: `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439`
- Drive target: `AI-Model-Vault/rag/Qwen__Qwen3-Embedding-0.6B-GGUF/`

### Reranker

- source: `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF`
- file: `qwen3-reranker-0.6b-q8_0.gguf`
- exact bytes: 639,153,184
- SHA256: `22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48`
- Drive target: `AI-Model-Vault/rag/ggml-org__Qwen3-Reranker-0.6B-Q8_0-GGUF/`

The notebook supports resumable `.part` downloads and writes `MODEL_READY.json` only after exact size, SHA256 and GGUF header validation.

## Runtime isolation

Chat server:

- port: 8080
- existing `RuntimeState`

Task server:

- port: 8091
- new `TaskRuntime`
- one task model at a time
- Drive cache is shared with chat
- process lifecycle is independent from chat

Default task limits:

- CPU threads: `max(1, CPU count - 2)`
- GPU layers: 0
- context: 4096
- batch: 4096
- ubatch: 4096
- parallel: 1

The large batch/ubatch setting is deliberate: pooled embeddings/reranking require a full input sequence to fit in one ubatch.

## Embedding adapter

Server flags:

`--embedding --pooling last`

Runtime API:

- `POST /v1/tasks/start`
- `GET /v1/tasks/status`
- `POST /v1/tasks/embeddings`
- `POST /v1/tasks/stop`

Bridge forwards embedding work to task-server `/v1/embeddings`.

The web workspace accepts one text per line and reports vector count, vector dimension and a short preview instead of dumping full vectors into the UI.

## Reranker adapter

Server flags:

`--embedding --reranking --pooling rank`

Current llama.cpp accepts both `--rerank` and `--reranking`.

Runtime API:

- `POST /v1/tasks/start`
- `GET /v1/tasks/status`
- `POST /v1/tasks/rerank`
- `POST /v1/tasks/stop`

Bridge forwards rerank work to task-server `/v1/rerank`.

The web workspace accepts one candidate document per line plus a query and `top_n`.

## Model index routing

The registry still identifies these models as RAG task models. If the fixed GGUF actually exists in the canonical `rag` directory, the model index overrides the older Transformers runtime label and routes only these two model IDs to the dedicated llama.cpp task server.

They never receive the ordinary chat `directLaunch` flag.

When the weight is absent, the card shows **准备 Embedding / Reranker** and opens the Drive bootstrap notebook.

## State boundary

Implementation readiness and weight readiness remain separate.

This implementation does not claim the two GGUF files are already present in the Drive vault. Direct task use only appears after a rescan sees the exact expected file and llama-server is detected.

Real endpoint validation still requires running the Drive bootstrap and exercising actual vectors/rerank scores with the user's Windows llama-server build.
