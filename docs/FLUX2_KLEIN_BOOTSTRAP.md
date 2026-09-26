# FLUX.2 Klein 4B Drive Bootstrap

## Purpose

`FLUX.2 Klein 4B FP8` already has its main transformer in Google Drive, but the official ComfyUI distilled text-to-image workflow also requires a Qwen text encoder and the FLUX.2 VAE.

The project therefore treats FLUX.2 as a fixed three-file package. It must not become directly runnable merely because the 4.07 GB FP8 transformer exists.

## Required files

| Role | File | Drive/runtime destination | State before bootstrap |
| --- | --- | --- | --- |
| diffusion model | `flux-2-klein-4b-fp8.safetensors` | `models/diffusion_models/` | already present in Drive |
| text encoder | `qwen_3_4b.safetensors` | `models/text_encoders/` | missing until bootstrap runs |
| VAE | `flux2-vae.safetensors` | `models/vae/` | missing until bootstrap runs |

Known SHA256 values used by the Drive bootstrap:

- FP8 transformer: `97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6`
- Qwen text encoder: `6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a`
- FLUX.2 VAE: `d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5`

## Drive bootstrap

The bootstrap notebook is stored in Drive:

`AI-Model-Vault/notebook_launchers/补齐_FLUX2_Klein_4B_ComfyUI_依赖_DriveFirst.ipynb`

It targets:

`AI-Model-Vault/image_base/black-forest-labs__FLUX.2-klein-4b-fp8/`

Behavior:

1. mount Google Drive;
2. verify the existing FP8 transformer by size and SHA256;
3. install bounded Hugging Face download tooling in Colab temporary storage;
4. download only the two missing companion files;
5. verify each companion by SHA256 before copying it to Drive;
6. verify all three final Drive files again;
7. write `COMFY_DEPENDENCIES_READY.json`;
8. remove temporary Colab download cache.

The bootstrap never downloads another copy of the 4.07 GB transformer.

## Runtime adapter

Runtime v0.14 uses the official ComfyUI FLUX.2 Klein 4B distilled graph shape:

- `UNETLoader`
- `CLIPLoader` with `type=flux2`
- `VAELoader`
- `CLIPTextEncode`
- `ConditioningZeroOut`
- `CFGGuider`
- `RandomNoise`
- `KSamplerSelect`
- `Flux2Scheduler`
- `EmptyFlux2LatentImage`
- `SamplerCustomAdvanced`
- `VAEDecode`
- `SaveImage`

Default distilled parameters are:

- 1024 x 1024
- 4 steps
- CFG 1.0
- Euler sampler

The Runtime validates exact filenames and minimum sizes before it starts any image job. It then downloads/resumes those exact Drive files through the normal Drive API cache, links them into the managed ComfyUI model directories, verifies that all required core nodes exist, queues the fixed graph, polls progress, and returns the generated image to the website.

## Web behavior

Before companions exist, the FLUX.2 card shows **补齐 FLUX.2 依赖**.

After the bootstrap finishes and the vault is rescanned, the same card can show **使用图像模型**, subject to Runtime hardware checks.

## Hardware boundary

The current local managed-ComfyUI path uses a conservative 12 GB VRAM startup floor and 16 GB free-cache floor for this adapter.

Those are startup guards, not guarantees that every custom size or setting fits in memory.

AMD/no-NVIDIA clients can use the separately implemented private remote NVIDIA Runtime path.

## Validation boundary

The adapter, exact file gate, official graph translation, bootstrap notebook, tests, and package smoke coverage can be verified in CI.

Full completion still requires:

- run the Drive companion bootstrap;
- confirm both companion files and the ready sidecar appear in the canonical FLUX.2 folder;
- rescan the website;
- run at least one real image generation on supported NVIDIA hardware or the private remote NVIDIA Runtime;
- record the observed VRAM/RAM/disk behavior and returned image.

Until then the project state is **adapter implemented / Drive companions not yet materialized / real generation validation pending**.
