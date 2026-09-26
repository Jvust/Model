# Project State

Updated: 2026-09-26

## Mission

Build a Drive-first AI model website where Google Drive remains the model vault, the website discovers/classifies model packages, and local inference runs without requiring Google Drive for desktop.

## Current state

Status: **v0.14 FLUX.2 Klein adapter + companion bootstrap prepared / hardware validation pending**

Verified on the live site before this branch:

- GitHub Pages deployment works.
- Google OAuth returns to the Model site.
- Google Drive API authorization works.
- `AI-Model-Vault` is auto-discovered.
- A real Drive scan completed across 203 folders and found 80 model-weight files.

Implemented on this branch:

- Added one-time `runtime/Install.cmd` for normal Windows use.
- Packaged Windows builds include standalone `ModelRuntime.exe`; user Python is not required.
- Runtime installs under `%LOCALAPPDATA%\JvustModel\app`.
- Current-user Windows auto-start is registered under HKCU.
- A tray Runtime keeps the localhost engine alive and restarts it after failures.
- The website automatically reconnects to localhost Runtime; manual checking is no longer the normal flow.
- The main UI hides the technical Runtime URL/logs under advanced diagnostics.
- Added `runtime/Uninstall.cmd`.
- Removed the Google Drive desktop mount requirement from the Windows launcher.
- First-run setup now asks only for `llama-server.exe`.
- Runtime cache defaults to `D:\Model` on Windows.
- Browser syncs the short-lived Google Drive access token to localhost Runtime memory only.
- Runtime accepts Drive file metadata by file ID.
- GGUF files can be downloaded directly from Google Drive API to local cache.
- Partial `.part` downloads can resume with HTTP Range.
- Cached models are reused on later launches.
- OAuth access tokens are not written to cache metadata or config.
- GGUF launch automatically continues after the Drive download completes.
- Runtime status exposes download bytes/progress.
- Existing multi-backend model-package routing remains in place.
- Managed ComfyUI video Runtime added.
- Official ComfyUI templates vendored for Wan2.2 TI2V 5B and HunyuanVideo 1.5 720p T2V.
- Web video workspace added with prompt/settings/progress/video playback.
- Runtime exposes /v1/video/generate, /v1/video/status, /v1/video/stop and ranged video output.
- First video run can install pinned ComfyUI Windows Portable automatically.
- Video workflow dependencies are downloaded lazily and cached locally.
- Workflow graph is pruned to the selected output, so Hunyuan's optional 1080p SR branch is not downloaded for the base 720p output.
- Pony Diffusion V6 XL now has a dedicated web image adapter using the verified Drive checkpoint and a fixed SDXL ComfyUI graph.
- Qwen-Image 2.1 GGUF now has a dedicated linked-Drive adapter using its existing root-folder weights; the website mounts that external Drive tree into the in-memory model index without duplicating files.
- Qwen adapter validates all three fixed artifacts (GGUF UNet, Qwen3-VL text encoder, VAE), installs ComfyUI-GGUF on first use, and uses the proven Qwen Image API graph from the existing notebook.
- Mixed image packages containing `.gguf` are routed by registry/runtime metadata rather than misclassified as llama.cpp chat models.
- Runtime exposes `/v1/image/generate`, `/v1/image/status`, `/v1/image/stop` and image-file return.
- Image/video direct-use actions are gated by managed-ComfyUI hardware status before large downloads begin.
- Runtime hardware status now reports nvidia-smi detection, CUDA version, GPU/VRAM, system RAM and cache-disk free space.
- GGUF launch planning/start now checks cache disk, configured CPU threads and available physical memory before downloading/starting.
- Empty canonical Vault category folders now exist for llm, reasoning, code, multimodal, ocr, rag, timeseries and novel; they do not count as model availability.
- Default-chat bootstrap notebook is stored in Drive under `AI-Model-Vault/notebook_launchers`; it targets official Qwen3-0.6B Q8_0, verifies SHA256/GGUF header, and writes into the canonical `llm` category.
- The website offers the bootstrap action only when no real chat GGUF is scanned; this does not mark the model ready before the weight actually exists.
- Unregistered GGUF packages under explicit chat-like Vault roots can route to llama.cpp, while mixed image GGUF packages remain protected by category/runtime routing.
- Dedicated llama.cpp task server added on localhost port 8091 for Qwen3 Embedding/Reranker GGUFs; chat remains on 8080 and is not replaced.
- Embedding/Reranker task APIs and real web workspace are implemented; Drive bootstrap remains required before the two task weights become runnable.
- Task routing overrides the older Transformers registry label only for the two fixed Qwen task model IDs when a GGUF is actually scanned under the RAG vault.
- The current managed ComfyUI package remains NVIDIA Windows/CUDA based; AMD-only machines are reported as hardware unsupported.
- FLUX.2 Klein 4B FP8 now has a fixed distilled 4-step ComfyUI adapter. Its existing 4.07GB BFL FP8 single-file is valid; direct use stays blocked until `qwen_3_4b.safetensors` and `flux2-vae.safetensors` are present in the same Drive package.
- A Drive-first FLUX.2 companion bootstrap notebook is stored under `AI-Model-Vault/notebook_launchers`; it validates official sizes/SHA256 and writes `MODEL_READY.json` only after all three files pass.
- Other ComfyUI/Diffusers model families remain future adapters.

## Architecture invariants

1. Google Drive remains canonical model storage.
2. Google Drive for desktop is not required.
3. GitHub stores code/governance only, never model weights or OAuth secrets.
4. OAuth access tokens passed to localhost Runtime are memory-only.
5. Runtime binds to localhost and enforces allowed browser origins.
6. Model cache is local implementation detail and may be deleted/rebuilt.
7. A Drive file ID is never used directly as a filesystem path.
8. GGUF direct launch requires a complete verified local cache file.
9. A model format extension alone does not imply that arbitrary non-GGUF files are executable.
10. Model-family-specific adapters are required for ComfyUI / Diffusers / Transformers / PyTorch families.
11. Remote Runtime must preserve localhost-only binding; tailnet publication is handled by Tailscale Serve, never by binding Runtime to LAN/public interfaces.
12. Remote non-loopback browser connections require HTTPS; the website does not persist the Runtime token beyond `sessionStorage`.
13. A remote Runtime may receive the user's short-lived Drive token only over the configured HTTPS connection and must keep it memory-only.

## Normal user flow

    One-time: Install.cmd
       ↓
    Windows login auto-starts tray Runtime
       ↓
    Open Model website
       ↓
    Connect Google Drive
       ↓
    Scan AI-Model-Vault
       ↓
    Select GGUF model
       ↓
    Browser sends temporary Drive token + file ID to localhost
       ↓
    Runtime downloads/resumes file into local cache
       ↓
    Validate GGUF header
       ↓
    Launch llama-server from cache
       ↓
    Chat in the website

## Current backend state

| Backend | Detection | Automatic launch |
| --- | --- | --- |
| llama.cpp | yes | chat via Drive API cache + dedicated embedding/reranker task server on 8091 |
| ComfyUI | managed/runtime detection + hardware gate | Pony Diffusion V6 XL + Qwen-Image 2.1 GGUF + FLUX.2 Klein 4B FP8 image, Wan2.2 TI2V 5B + HunyuanVideo 1.5 T2V on supported NVIDIA Windows hardware; FLUX.2 direct use also requires its two companion files |
| Remote NVIDIA transport | Tailscale Serve + Runtime token | implemented; real two-device validation pending |
| Diffusers | yes | not yet |
| Transformers | yes | not yet |
| PyTorch | yes | not yet |
| ONNX Runtime | yes | not yet |
| TFLite | yes | not yet |

## Next technical steps

1. Validate Pony Diffusion V6 XL and Qwen-Image 2.1 GGUF end-to-end on a supported NVIDIA Windows Runtime and record VRAM/RAM/disk observations.
2. Run/validate the prepared FLUX.2 companion bootstrap, confirm encoder + VAE appear beside the existing FP8 main file, rescan, then validate one real 1024×1024 / 4-step image on a supported NVIDIA Runtime.
3. Run/validate the prepared Qwen3-0.6B Q8_0 Drive bootstrap, confirm the verified GGUF appears under `llm/Qwen3-0.6B-GGUF`, then validate the full Drive cache → llama.cpp → web-chat round trip.
4. Run/validate the prepared Qwen3 Embedding/Reranker bootstrap and exercise real `/v1/embeddings` + `/v1/rerank`; then continue Stage 4 with GOT-OCR and Chronos/TimesFM.
5. Validate the implemented Tailscale Serve remote NVIDIA path on two real devices (AMD/no-NVIDIA client + separate NVIDIA Windows host), including Drive session sync, progress polling and returned image/video media.
6. Extend video adapters to Wan2.2 T2V/I2V/Animate and additional verified model families.
7. Keep cache management/cancellation coverage and evaluate sparse-cache/virtual filesystem access only after full-file paths are stable.
