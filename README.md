# Model

Drive-first local AI website.

Google Drive is the canonical model vault. The website discovers model packages with Google Drive API, while the local Windows Runtime downloads selected models directly from Drive API into a local cache and runs them on the current computer.

**Google Drive for desktop is not required.**

## Qwen-Image-Edit-2511 reference workspace (Runtime v0.17)

The website now discovers the exact `Qwen-Image-Edit-2511` folder even when it is nested outside `AI-Model-Vault`. Its complete `model/` directory is recognized as a Diffusers package, not a single ComfyUI model file.

Select **使用 2511 参考图编辑**, check the separate CUDA/Diffusers environment, then prepare the existing Drive package. Upload a PNG/JPEG/WebP and choose a subject-and-clothing-preserving background or lighting preset. The workspace supports seed, cancellation, original/result comparison and PNG download. Reference conditioning does not guarantee unchanged style or details; inspect the result.

The packaged bridge does not bundle PyTorch or model weights. Configure `MODEL_DIFFUSERS_PYTHON` to a compatible CUDA Python environment. See [setup and limitations](docs/QWEN_2511_REFERENCE_EDIT.md). Local tests cover indexing, API validation and worker lifecycle using fixtures, not actual GPU image quality.

## Drive package manifests

For the improved existing video workspace (validated parameters, clip duration/seed, accurate stop state, authenticated preview/download and owned history output), see [Video Runtime v0.17](docs/VIDEO_WORKSPACE_V17.md). New 14B/I2V adapters are not implied by this update.

Directory-based model runtimes need more than weight files. Index v3 also preserves small JSON/TXT/YAML/YML support metadata and exposes `supportFiles` + `manifestFiles` without changing the existing weight-only `files` contract.

Runtime v0.16 can reconstruct a full Drive package under `D:\Model\packages` through:

    GET  /v1/packages/status
    POST /v1/packages/materialize
    POST /v1/packages/stop

The materializer reuses the resumable Drive cache and hard-links large cached artifacts into the package directory, so future Diffusers/Transformers workers do not need to redownload a repository or keep a second giant copy.

This is specifically required for the existing Wan2.2 T2V/I2V Drive packages: their high/low-noise models are sharded Diffusers directories with index/config files, not single ComfyUI UNET files.

See `docs/DRIVE_PACKAGE_MANIFESTS.md`.
## Embedding and reranker task runtime

Qwen3 Embedding 0.6B and Qwen3 Reranker 0.6B use a dedicated llama.cpp task server instead of adding PyTorch/Transformers to the Windows Runtime package.

- chat llama-server: port 8080;
- task llama-server: port 8091;
- Embedding: `--embedding --pooling last` → `/v1/embeddings`;
- Reranker: `--embedding --reranking --pooling rank` → `/v1/rerank`;
- task context/batch/ubatch: 4096 / 4096 / 4096, parallel 1;
- task GPU layers default to 0 so CPU-only machines can use the first version.

Drive bootstrap:

    AI-Model-Vault/notebook_launchers/启动_Qwen3_Embedding_Reranker_0.6B_Q8_0_DriveFirst.ipynb

The web card shows the bootstrap action while the exact task GGUF is missing. After a successful rescan, **使用 Embedding** or **使用 Reranker** opens the real vector workspace.

See `docs/QWEN_TASK_RUNTIME.md`.
## Default chat bootstrap

The Runtime chat path is already implemented, but the canonical Drive vault may still have no actual chat GGUF. In that state the model library shows **准备默认聊天模型** instead of pretending a registry-only model is runnable.

The Drive artifact:

    AI-Model-Vault/notebook_launchers/启动_Qwen3-0.6B_Q8_0_DriveFirst.ipynb

prepares the official **Qwen3-0.6B Q8_0** GGUF as a small end-to-end validation model. It writes to:

    AI-Model-Vault/llm/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf

The notebook verifies the official SHA256 and the fixed GGUF header before writing `MODEL_READY.json`. After it completes, rescan the vault; the website can recognize an unregistered GGUF inside an explicit chat category and expose the normal llama.cpp **使用模型** action.

This bootstrap model is for validating the local chat pipeline. It does not replace the larger registered Qwen / DeepSeek / Coder / writing models.

See `docs/DEFAULT_CHAT_BOOTSTRAP.md` for the exact invariant and verification flow.
## Current flow

    Open Model website
          ↓
    Connect Google Drive
          ↓
    Auto-find AI-Model-Vault
          ↓
    Scan model metadata
          ↓
    Select a GGUF
          ↓
    Browser sends temporary Drive session to localhost Runtime
          ↓
    Runtime downloads/resumes the GGUF into local cache
          ↓
    llama.cpp launches from cache
          ↓
    Chat in the same website

## One-time Windows install

For normal use, open the [Runtime package workflow](https://github.com/Jvust1/Model/actions/workflows/runtime-package-drive-api.yml), choose the latest successful run on `main`, download its `Model-Web-Runtime-…` artifact, extract it, then run:

    runtime\Install.cmd

The source repository does not contain `ModelRuntime.exe`; use the workflow artifact above, which includes the compiled executable. The packaged installer does **not** require a system Python installation.

The installer:

- uses the bundled standalone `ModelRuntime.exe`;
- asks for `llama-server.exe` only when it cannot find one automatically;
- copies the Runtime to `%LOCALAPPDATA%\JvustModel\app`;
- enables current-user Windows auto-start;
- starts a tray Runtime in the background;
- opens the Model website.

After that, normal use is simply:

    open https://jvust.github.io/Model/

The website automatically reconnects to the local Runtime. You do not need to run `Model.cmd` every time.

To remove the background Runtime:

    runtime\Uninstall.cmd

Use `runtime\Model.cmd` only as a manual/debug launcher.

Default model cache:

    D:\Model

You can override it with `MODEL_CACHE_ROOT` or `runtime\start_bridge.ps1 -CacheRoot "E:\ModelCache"`.

Runtime config:

    %LOCALAPPDATA%\JvustModel\runtime.json

Runtime logs:

    %LOCALAPPDATA%\JvustModel\logs

## Drive session security

The browser sends the current short-lived Google Drive access token to:

    http://127.0.0.1:8765/v1/drive/session

The Runtime keeps it in memory only. It is not persisted in config or cache metadata.

## Download/cache behavior

GGUF models are downloaded directly through Google Drive API.

- existing complete cache → reuse immediately;
- interrupted download → resume from `.part` when Drive honors Range;
- completed file → validate GGUF header, then launch llama.cpp.

Large models still need enough local disk space for the cache. The desktop Drive app is not needed.

## 当前实际可用状态

模型登记、Drive 文件和运行适配器是三个不同状态。网页会展示登记表中的全部模型，但只有同时满足“Drive 主库有权重”和“对应运行适配器已接通”的模型才会出现启动入口。

目前已经接通网页 Runtime adapter 的模型包括 Pony Diffusion V6 XL、Qwen-Image 2.1 GGUF、FLUX.2 Klein 4B FP8 图像工作流、Wan2.2 TI2V 5B 和 HunyuanVideo 1.5 视频工作流，以及 Drive 中实际存在聊天 GGUF 文件时的 llama.cpp 聊天链路。FLUX.2 只有在两个 companion 文件补齐后才显示直接使用。OCR、Embedding、Reranker、时间序列及尚未完成依赖验证的图像模型继续只显示运行方案。

模型卡片上的“查看运行方案”只会读取本机后端和模型状态，不会把任意 .safetensors、.pth 或 .ckpt 文件假设成可以直接启动的模型。这样可以避免下载大量文件后才发现缺少 VAE、文本编码器、预处理器或工作流。

## Backend routing

Current targets:

- GGUF → llama.cpp
- image/video → ComfyUI / Diffusers
- OCR/multimodal/RAG → Transformers
- time-series → PyTorch
- ONNX → ONNX Runtime
- TFLite → TFLite

Automatic web adapters now include:

- GGUF → Drive API cache → llama.cpp → web chat.
- Pony Diffusion V6 XL → Drive API cache → managed ComfyUI SDXL workflow → web image workspace.
- Qwen-Image 2.1 GGUF → linked Drive folder → Drive API cache → managed ComfyUI + ComfyUI-GGUF → web image workspace.
- FLUX.2 Klein 4B FP8 → exact three-file Drive package → managed ComfyUI distilled 4-step workflow → web image workspace.
- Wan2.2 TI2V 5B → managed ComfyUI → web video workspace.
- HunyuanVideo 1.5 T2V → managed ComfyUI → web video workspace.

For the video adapters, the Drive package remains the catalog/model identity, while Runtime caches the official ComfyUI-compatible backend artifacts required by the selected workflow. This is necessary because the Drive-native training/inference package layout is not identical to ComfyUI's repackaged model layout.

The managed ComfyUI image/video path currently targets NVIDIA Windows systems. Runtime checks hardware before exposing the direct-use action; unsupported machines stay on the run-plan path and do not start a large model download.

First video use may download:

- ComfyUI Windows Portable (NVIDIA cu126);
- several large model artifacts required by the official workflow.

Those files are cached under `D:\Model\video` by default and reused later. Set `MODEL_VIDEO_ROOT` to override the video cache only.

See `docs/MULTI_BACKEND.md` for the remaining family adapters.

## Image workspace

Select an adapted image model and click **使用图像模型**. Runtime validates every fixed artifact declared by that adapter, downloads/resumes only the selected Drive files into the persistent `D:\\Model` cache, prepares managed ComfyUI, submits the fixed workflow and streams the generated image back to the webpage.

Current direct image adapters:

- Pony Diffusion V6 XL — 1024×1024 default, 28 steps, CFG 5, CLIP skip 2.
- Qwen-Image 2.1 GGUF — 768×768 default, 20 steps, CFG 1.0; fixed files are `qwen-image-2.1-Q4_K_M.gguf`, `qwen3vl_8b_int8_convrot.safetensors`, and `qwen_image_2.1_vae_bf16.safetensors`.
- FLUX.2 Klein 4B FP8 — 1024×1024 default, distilled 4 steps, CFG 1.0; fixed files are `flux-2-klein-4b-fp8.safetensors`, `qwen_3_4b.safetensors`, and `flux2-vae.safetensors`.

Qwen-Image remains in its existing Drive root folder. The website links that folder into the model index at scan time instead of copying roughly 14 GB of weights into `AI-Model-Vault`. First Qwen use installs the small ComfyUI-GGUF custom node/dependencies into the managed ComfyUI runtime, then reuses them.

FLUX.2 Klein 4B FP8 now has a fixed Runtime adapter, but direct use remains gated by three exact Drive artifacts:

- `flux-2-klein-4b-fp8.safetensors` — existing official BFL FP8 main file;
- `text_encoders/qwen_3_4b.safetensors`;
- `vae/flux2-vae.safetensors`.

If either companion is missing, the model card shows **补齐 FLUX.2 依赖** and opens the Drive-first bootstrap notebook instead of starting a task. The notebook resumes downloads, verifies official SHA256 values, and never redownloads a valid main FP8 file.

FLUX.2 defaults: 1024×1024, 4 steps, CFG 1.0, Euler. See `docs/FLUX2_KLEIN_BOOTSTRAP.md`.

## Hardware preflight

Runtime v0.16 checks hardware before starting large local workloads.

- managed ComfyUI: detects `nvidia-smi`, CUDA version, GPU/VRAM and cache free space;
- Pony image safety floor: 8 GB VRAM;
- Qwen-Image 2.1 GGUF safety floor: 14 GB VRAM and 18 GB free cache space;
- FLUX.2 Klein 4B FP8 safety floor: 10 GB VRAM and 15 GB free cache space;
- Wan2.2 TI2V 5B safety floor: 12 GB VRAM;
- HunyuanVideo 1.5 safety floor: 16 GB VRAM;
- managed ComfyUI keeps at least 10 GB cache free space before launch;
- GGUF launch plans report cache disk, available system memory and configured CPU threads;
- Drive downloads verify the remaining model bytes plus a 2 GB cache reserve before transfer.

If NVIDIA/CUDA is unavailable, image/video models stay on the run-plan path and the UI reports **需要远程 NVIDIA Runtime** instead of starting a large local download. These thresholds are startup safety floors, not guarantees that every resolution/step configuration will fit.

## Remote NVIDIA Runtime

Devices without a usable local NVIDIA CUDA path can point the same website at another NVIDIA Windows machine without changing model/task APIs.

On the NVIDIA host:

    runtime\Enable-Remote-Nvidia.cmd

This keeps Model Runtime bound to `127.0.0.1:8765`, exposes it privately through **Tailscale Serve** on dedicated HTTPS port `8443`, generates a random Runtime token, and restarts the local Runtime with remote authentication enabled.

On the client, open **高级诊断**, set **Runtime Bridge** to the printed `https://<node>.<tailnet>.ts.net:8443` address, enter the Runtime token, and click **重新连接**.

Security properties:

- Tailscale Serve only, never Funnel;
- Runtime continues to listen on localhost only;
- tailnet ACLs remain in force;
- protected Runtime API calls require a 256-bit token;
- the browser keeps the Runtime token only in `sessionStorage`;
- non-loopback Runtime URLs must use HTTPS;
- Drive OAuth access tokens are still memory-only inside Runtime.

Disable the remote listener with:

    runtime\Disable-Remote-Nvidia.cmd

See `docs/REMOTE_NVIDIA_RUNTIME.md` for the architecture, token rotation, and validation boundary.
## Video workspace

Select an adapted video model in the model library and click **使用视频模型**.

The web workspace exposes prompt, negative prompt, resolution, frames, FPS, steps, CFG and seed. Runtime then prepares ComfyUI, queues the official workflow, tracks progress and returns the generated video directly to the browser.

Current direct video adapters:

- Wan2.2-TI2V-5B
- HunyuanVideo-1.5

Video generation is hardware-intensive; successful execution depends on available GPU/VRAM/RAM/disk and the selected settings.

## Website

Production:

    https://jvust.github.io/Model/

## Source of truth

- GitHub: code, governance, current project state.
- Google Drive: model artifacts and model metadata registry.

## AI 工作区

网站现在提供统一的多工作区入口：

- 本机聊天：GGUF / llama.cpp
- 本机视频：Wan2.2、HunyuanVideo
- 图像生成：Pony Diffusion V6 XL、Qwen-Image 2.1 GGUF、FLUX.2 Klein 4B FP8 adapter 已接通；FLUX.2 companion 未齐时显示依赖补齐入口
- 图像编辑：局部重绘、扩图、放大
- 视觉 / OCR：Transformers 运行方案
- 向量检索：Embedding / Rerank 知识库入口
- 时序预测：PyTorch 预测入口

图像、视觉、检索和时序工作区会先检查本机后端，并保留模型家族适配边界；Pony、Qwen-Image 与文件齐全后的 FLUX.2 会复用同一图像任务/结果回传接口。模型库卡片仍是模型包与 Drive 元数据的唯一来源。
