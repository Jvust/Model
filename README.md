# Model — Drive-first AI Runtime

## 0.18 optional Drive Desktop source

New: [Drive Desktop source guide](docs/DESKTOP_SOURCE.md). The original API mode remains available. Desktop mode uses a native Windows directory picker, metadata-only scans and read-only, resumable local copies into the Runtime cache; it does not require the web OAuth Worker. Hardware/model adapters remain unchanged.

### Previous 0.17 consolidated release candidate

**Start with [`docs/使用与验收.md`](docs/使用与验收.md).**

Google Drive is the canonical model vault. GitHub stores source and governance; `D:\Model` is a reusable local cache. Google Drive Desktop is not required. Model capability, actual weight presence, hardware suitability and real execution evidence are separate states.

The release workflow builds a matching Windows executable, embedded local website and checksum-verified official CPU llama.cpp engine. Run `runtime/Install.cmd` after extracting the complete release ZIP. Open `http://127.0.0.1:8765/`; the candidate no longer depends on production GitHub Pages serving the newest HTML.

The optional private NVIDIA path remains Tailscale Serve + HTTPS + Runtime token. Installing the CPU package does not turn AMD hardware into a supported NVIDIA host.

## Implemented paths

| Task | Implementation | Evidence boundary |
| --- | --- | --- |
| Chat | Drive GGUF cache, llama.cpp, per-model context/CPU/GPU/load-mode profiles | Small-model CPU smoke; not every registered large model |
| Embedding / Reranker | Dedicated llama.cpp task server on 8091; full result JSON export | Separate exact GGUFs required; no ordinary chat routing |
| Image | Pony SDXL, Qwen-Image GGUF and FLUX.2 fixed ComfyUI workflows | Correct files and supported NVIDIA host required |
| Existing video | Wan TI2V 5B and HunyuanVideo ComfyUI | Real intended-GPU acceptance remains separate |
| OCR | Native HF GOT-OCR 2.0, isolated CPU worker | Real model output is tested; OCR accuracy is not guaranteed |
| Forecast | Chronos-2 and TimesFM 2.0, isolated CPU workers | Actual forecasts; no simulated fallback or financial guarantee |
| Additional video | Full Wan2.2 T2V/I2V Diffusers packages | Not interchangeable with a single-file ComfyUI checkpoint |

Missing or unsupported models remain blocked with a reason; registry entries alone never count as installed models.

## Preparation and deployment

`notebooks/Prepare_Models.ipynb` prepares six CPU model packages in the canonical Drive directories. It resolves immutable repository revisions and verifies sizes and available LFS SHA256s. Use CPU in Colab, review the size preview, then run the download cell. A notebook merely existing in Drive is not a completed download.

OAuth Worker v2 source is included in `workers/drive-oauth-bridge.mjs`. It fixes migrated callback addresses and isolates/encrypts refresh tokens per session. **Committing it does not deploy it to Cloudflare.** Existing bindings and secrets must remain in Cloudflare; never commit or share them. The public deployment probe and actual user authorization are distinct checks.

## Development / testing

```text
python -m runtime.application
python -m unittest discover -s tests -v
node tests/test_oauth_sessions.mjs
```

Actual native CPU model tests: `python -m tools.smoke_native_models --model got_ocr2` (or `chronos_2`, `timesfm_2_0_500m`).
Actual Windows GGUF tests: `python -m tools.prepare_llama`, then `python -m tools.smoke_gguf_models`.
These tests download official weights to ephemeral test storage, not the user's Drive.

## Authoritative state

- [Model enablement plan](MODEL_ENABLEMENT_PLAN.md)
- [Current project state](governance/PROJECT_STATE.md)
- [Release state](governance/RELEASE_STATE.json)
- [Release acceptance boundaries](docs/RELEASE_ACCEPTANCE.md)
- [Chinese usage and acceptance guide](docs/使用与验收.md)
- [Qwen task runtime](docs/QWEN_TASK_RUNTIME.md)
- [FLUX.2 preparation](docs/FLUX2_KLEIN_BOOTSTRAP.md)

This candidate is not declared a fully accepted deployment until actual Drive authorization/cache tests, intended-host GPU image/video tests, and the two-device private Runtime flow pass. Main and production deployment are not silently merged or changed.
