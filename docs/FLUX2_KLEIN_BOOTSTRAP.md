# FLUX.2 Klein 4B FP8 Drive-first Enablement

## Purpose

Enable the existing BFL FLUX.2 Klein 4B FP8 single-file weight in the Model web image workspace without redownloading the main 4.07 GB diffusion file.

## Canonical Drive package

`AI-Model-Vault/image_base/black-forest-labs__FLUX.2-klein-4b-fp8`

Required files:

| Role | Relative path | Expected bytes | SHA256 |
| --- | --- | ---: | --- |
| diffusion | `flux-2-klein-4b-fp8.safetensors` | 4,070,624,520 | `97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6` |
| text encoder | `text_encoders/qwen_3_4b.safetensors` | 8,044,982,048 | `6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a` |
| VAE | `vae/flux2-vae.safetensors` | 336,213,556 | `d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5` |

The main diffusion file already exists in Drive. The encoder and VAE are companion components and are not considered optional for the fixed ComfyUI workflow.

## Drive bootstrap

The bootstrap notebook is stored in Drive:

`AI-Model-Vault/notebook_launchers/启动_FLUX2-klein-4b-fp8_补齐依赖_DriveFirst.ipynb`

It:

1. validates the existing BFL FP8 main file instead of replacing it;
2. downloads only the missing Comfy-Org encoder and VAE;
3. uses `.part` files and HTTP Range for resumable transfers;
4. checks exact file size and SHA256;
5. writes `MODEL_READY.json` only after all three files pass verification.

The website shows **补齐 FLUX.2 依赖** while one of the fixed artifacts is absent or does not match its exact official byte size. This state is intentionally different from **使用图像模型**.

## Runtime adapter

Runtime v0.14 adds the `flux2_klein_4b_fp8` image adapter.

Default task settings:

- 1024×1024
- 4 steps
- CFG 1.0
- Euler sampler
- random seed when seed is omitted

Fixed ComfyUI API graph:

1. `UNETLoader`
2. `CLIPLoader(type=flux2)`
3. `VAELoader`
4. `CLIPTextEncode`
5. `ConditioningZeroOut`
6. `RandomNoise`
7. `KSamplerSelect`
8. `Flux2Scheduler`
9. `CFGGuider`
10. `EmptyFlux2LatentImage`
11. `SamplerCustomAdvanced`
12. `VAEDecode`
13. `SaveImage`

Before queueing a job, Runtime queries ComfyUI `/object_info` and refuses execution if any required node is missing.

The managed ComfyUI baseline remains pinned to v0.37.0. That version contains `Flux2Scheduler` and `EmptyFlux2LatentImage`, so no ComfyUI baseline upgrade is required for this adapter.

## Hardware/cache gate

Current conservative startup floors:

- NVIDIA/CUDA managed ComfyUI path;
- 10 GB VRAM;
- 15 GB free cache space.

These are safety floors, not guarantees that every resolution or concurrent workload will fit.

## State boundary

The code adapter can be complete while the Drive package is still incomplete.

Do not mark FLUX.2 as directly usable until the Drive scan sees all three fixed artifacts at their exact expected byte sizes. The web card must continue to show the bootstrap action while companions are missing.

Real image-generation validation still requires a supported NVIDIA Runtime (local or private remote Runtime).
