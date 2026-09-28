# Model 0.19 · 启用与接续候选版

**这是源码候选包，不是已经完成 Windows / NVIDIA 实机验收的正式安装包。**

本轮建立统一“选择模型 → 检查 → 准备 → 运行 → 逐项保存 → 下次接续”入口；继承0.18的 Drive API / Drive Desktop、缓存、聊天及专用后端。

## 在 Y9000P 上打开

1. 完整解压到 D 盘，例如 `D:\Model-App-0.19`，不要覆盖旧0.18安装目录。
2. 如果旧 Model 托盘正在运行，先正常退出它。双击根目录 **`Run-Model-0.19.cmd`**。
3. 启动器把私有 Python / 辅助库放到 `D:\Model`，打开本机网页。**本候选包不要运行旧 `runtime/Install.cmd`**，旧安装入口要求另行构建的新 EXE。
4. 选择 Drive 来源并扫描 `AI-Model-Vault`；从模型卡片点 **“启用/接续”**。支持且检查通过的模型可以“只准备，下次复用”或“启用并运行”。
5. 下次使用同一启动器、同一本地缓存，重新连接来源并扫描后，在“历史任务与接续”中继续未完成任务。

关闭窗口会停止本次源代码 Runtime，但不会删除 `D:\Model` 的已完成权重、任务数据库、输入或结果。首次准备辅助运行环境仍需联网；缓存完整后的推理不需要重新下载模型权重。Drive API 模式重新授权仍可能需要联网。

包含历史0.18验证包中的官方 CPU llama.cpp 引擎及许可证；不把旧0.18 `ModelRuntime.exe` 冒充新程序。Windows启动器、构建脚本、真实GPU生成仍待目标机/CI验收。

## 本轮状态

- 代码和测试已经在本次工作目录实现；原有 GitHub工作分支仍在基线提交，**新改动尚未推送到 GitHub，也尚未上传Drive**。
- 具体已测项、验收边界和缺口见 `docs/ACTIVATION_AND_RESUME.md`、`docs/EVALUATION_0_19.md`。
- 跨会话接手入口：`docs/HANDOFF.md`、`governance/RELEASE_STATE.json`、`governance/pending_sync.json`。

---

## 历史 0.18 README（保留来源，不代表0.19实测）

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
