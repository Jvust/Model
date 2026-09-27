# Model Project State

Updated: 2026-09-27
Status: **0.18 optional read-only Drive Desktop source — API mode retained; intended-host acceptance pending**

Authority: `Jvust/Model`. The growth plan remains `MODEL_ENABLEMENT_PLAN.md`; this status does not redefine its completion criteria.
Baseline before this release work: `eb930ca76b74777435f204946e5dd11d4236ca8a`.
Working branch: `feat/model-release-v017-20260927`; PR #26. Main is not automatically merged.

## Architecture retained

Google Drive is the canonical model vault. Source, governance and decisions live in GitHub. Cache defaults to `D:\Model`, with explicit user configuration supported. OAuth tokens received by the Runtime remain memory-only. Drive file IDs are never trusted as filesystem paths.

The Runtime listens only on loopback. Private remote access uses Tailscale Serve and an explicit Runtime token over HTTPS; no public-interface binding or Funnel. All old/new result and media endpoints require the same configured token.

## New release composition

Entrypoint is `runtime/application.py`. The packaged EXE includes the exact corresponding local website and native worker source. Installation opens `http://127.0.0.1:8765/`, not an older production Pages build. The release includes a checksum-verified official CPU llama.cpp runtime; existing valid user engine selection is preserved.

Chat remains on 8080; embedding/reranker remains separate on 8091. Model-specific context, threads, GPU layers and load mode are persisted under the cache. Native inference environments are isolated and reused; system Python is not modified.

## Implemented task families

- GGUF chat with explicit chat-category routing and per-model settings.
- Fixed Qwen3 embedding and reranker GGUFs with real task APIs and full JSON export.
- Existing Pony, Qwen-Image and FLUX.2 image graphs with fixed-file and hardware gates.
- Existing Wan TI2V and Hunyuan video graphs.
- Native HF GOT-OCR 2.0, Chronos-2 and TimesFM 2.0 CPU workers with full-package validation.
- Full-package Wan2.2 T2V/I2V A14B Diffusers workers; GPU/offload resources required.
- Persistent native JSON/CSV/MP4 output, cancellation, manifest/config/shard validation.

All other registry-only or incompatible models remain blocked rather than routed to a plausible but incorrect loader. The native catalog is distinct from the legacy 27-model capability registry.

## Verification evidence

The initial 0.17 commit `11c028149c458b5b5a49e0ccf226cb7321437976` passed actual CPU GOT-OCR, Chronos-2 and TimesFM inference, real localhost-browser tests, source contracts and Windows executable packaging in Actions run `36267127694`. These used official temporary test weights, not user Drive credentials or the user's device. The OCR fixture returned repeated text; evidence preserves raw output and is not an accuracy benchmark.

Further delivery/profile/OAuth fixes add contract tests, a fresh installer check, and actual chat/embedding/reranker tests using the bundled Windows engine.

Runtime 0.18 Desktop source commit `0551384336d7282bb09c840535eea05b0f4e99a5` passed the normal CI run `36287020640` and the full packaging/browser/model regression run `36287020646`. The latter validated the v18 Windows EXE/package, real localhost Chromium API/Desktop-mode flows, and a real Qwen3 CPU chat through a temporary local-directory source with cold-copy then warm-cache reuse. These tests did not use the user's Google Drive Desktop mount, user device, or NVIDIA hardware.

## OAuth deployment gate

The old repository Worker stored a single global refresh token. Worker v2 fixes callback addresses, uses signed state + PKCE, and encrypts separate refresh tokens per browser session. Legacy global-token sessions require reauthorization rather than falling back to another user's grant.

The Worker source is committed but Cloudflare is a separate deployment. No Cloudflare account deployment was executed by this release change. A public unauthenticated probe does not establish successful user OAuth/Drive access.

## Remaining real-environment gates

1. Desktop-source path: select the user's actual Google Drive Desktop/DriveFS `AI-Model-Vault` on the intended Windows host and verify streamed placeholders, disconnect/reconnect behavior, cold-cache/resume/warm-cache reuse, and source-file preservation.
2. API/OAuth path: deploy and verify OAuth Worker v2 with existing account-owned bindings/secrets and complete actual Drive authorization. This no longer blocks Desktop mode.
3. Run a real image/video on supported NVIDIA hardware and save inputs, model revisions, outputs and resource observations.
4. Verify the two-device private HTTPS session including authenticated media results.
5. Review/authorize release merge. Source completion, Actions success, Drive weight presence, and intended-host acceptance must not be conflated.

See `docs/使用与验收.md`, `docs/RELEASE_ACCEPTANCE.md` and `governance/RELEASE_STATE.json`.

## 0.18 Desktop source extension

User now authorizes an additional Google Drive Desktop path. This does not remove the API-first option. DesktopSource grants one locally selected root; DesktopAwareCache resolves current scan handles and skips upstream OAuth only for those files. Cloud IDs still use the original credential provider. API and per-Runtime desktop indexes are isolated.

Validation: 196 Python tests pass, including 43 Desktop-source filesystem/HTTP cases. CI also passed 9 real localhost + Chromium Desktop-mode checks with no OAuth/Drive API requests, plus a real Qwen3 local-directory → cold Runtime cache → chat → warm-cache reuse test. The source file remained unchanged and `drive_api_session=false` during that test. This is strong local-filesystem compatibility evidence, not a claim that the user's actual Google Drive Desktop/DriveFS mount or intended GPU host has been tested. See `docs/DESKTOP_SOURCE.md`.
