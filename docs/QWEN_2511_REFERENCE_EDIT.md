# Qwen-Image-Edit-2511 reference editing

The existing `Qwen-Image-Edit-2511/model` Drive folder is an official **Diffusers package**, not a ComfyUI checkpoint. No weight conversion or new model download from Hugging Face is needed. Scanning discovers the accessible named folder even outside the selected vault and mounts its metadata under `image_edit`. Drive file IDs are discovered at runtime, never hardcoded.

## Website flow

1. Connect Drive and rescan. Choose **使用 2511 参考图编辑**.
2. **检查编辑环境** verifies the inference Python and CUDA without downloading weights. Missing dependencies do not count as ready.
3. **准备 Drive 模型包** uses the existing resumable cache/package materializer, including configs, tokenizer, processor, scheduler, VAE, text encoder, and all transformer shards. This may need substantial local disk space; preparation is explicit, not automatic.
4. Select a PNG/JPEG/WebP reference up to 8 MB. Choose a subject-preserving background or lighting preset and a seed. **开始编辑** sends the reference image to the local/authorized remote Runtime.
5. Compare original and result and download PNG. Stop cancels the owned worker. Switching tabs cleans up polling/object URLs but does not silently terminate the job.

This first implementation exposes only three non-explicit scene presets. The server rejects arbitrary prompt/instruction overrides. It is not a promise of pixel-identical style or identity preservation.

## Runtime setup

Update the Windows Runtime to **v0.17** from the build of this change. Its packaged bridge remains standalone; GPU inference uses a separate Python environment. Configure `MODEL_DIFFUSERS_PYTHON` to the absolute path of a Python with CUDA-enabled torch, diffusers supporting `QwenImageEditPlusPipeline`, transformers, accelerate, and Pillow. Restart the Runtime after configuration. The web environment check shows whether CUDA/dependencies are available. No dependencies or models are installed automatically.

The worker reads only the materialized local model directory with `local_files_only=True` and offline hub settings, using CPU offload. It never receives Drive access tokens. Inference needs a compatible NVIDIA machine, enough system RAM for the full model, and sufficient GPU memory; no hardware-independent VRAM guarantee is made.

Endpoints retain the bridge's existing origin and remote-token checks:

- `GET /v1/edit/environment`, `/v1/edit/status`, `/v1/edit/file?job_id=…`
- `POST /v1/edit/generate`, `/v1/edit/stop`
- Preparation reuses `/v1/packages/materialize`, `/v1/packages/status`, `/v1/packages/stop`.

Only `/v1/edit/generate` accepts the larger 12 MB JSON request limit. Existing endpoints retain the original 256 KB limit. Uploaded reference images are bounded and type-checked. Outputs can only be accessed with the current completed job ID.

## Verification scope

Unit tests exercise recognition, reference/manifest payloads, package validation, subprocess success/failure/cancellation, credential isolation, and invalid input rejection. Mock weights and a fixture subprocess do **not** prove actual 2511 GPU inference or exact style preservation. A real CUDA/Drive run remains necessary after environment setup.

Official API references:

- https://huggingface.co/Qwen/Qwen-Image-Edit-2511
- https://huggingface.co/docs/diffusers/api/pipelines/qwenimage#multi-image-reference-with-qwenimageeditpluspipeline
- https://developer.mozilla.org/en-US/docs/Web/API/FileReader/readAsDataURL
