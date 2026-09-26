# Model 0.17 — consolidated release candidate

## Status and scope

This is a **release candidate, not a claim of completed real-hardware acceptance**.
It extends `MODEL_ENABLEMENT_PLAN.md` without changing its definition of done: actual
weights, a suitable adapter, adequate resources, and a task that returns a genuine result.
Baseline: `Jvust/Model`, `eb930ca76b74777435f204946e5dd11d4236ca8a`.

### Implemented in this candidate

- A new composition entrypoint, `runtime/application.py`, preserving existing chat,
  image, video, cache, task and package routes.
- Isolated CPU-native GOT-OCR 2.0 HF, Chronos-2 and TimesFM 2.0 workers.
- Isolated NVIDIA Diffusers workers for full Wan2.2 T2V/I2V A14B packages. These use
  actual Diffusers sharded packages, **not** ComfyUI's single-file UNET loader.
- Private reusable dependency environments. System Python is not modified. Windows
  inference Python is bootstrapped automatically; Linux development uses a virtualenv.
- Complete manifest gates, resource preflight, cancellable jobs and persistent caches.
- Per-job JSON output, CSV forecasts, OCR text and authenticated video output.
- Token authentication on old and new result/media endpoints, including ranged reads.
- Frontend polling cancellation fix: cancelling does not abandon an unresolved Promise.
- Task-server download cancellation now aborts a stale download rather than merely
  hiding its progress; absence of llama-server blocks downloading first.

### Native worker contracts

| Registered ID | Required artifact family | Execution | User input | Result |
| --- | --- | --- | --- | --- |
| `got_ocr2` | `stepfun-ai/GOT-OCR-2.0-hf`, native `got_ocr2` config, processor, tokenizer, safetensors | CPU | PNG/JPEG/WebP, at most 8 MB | Text / JSON |
| `chronos_2` | complete `amazon/chronos-2` package | CPU | 16–2048 finite observations, horizon 1–256 | Relative-step predictions + model quantiles / CSV |
| `timesfm_2_0_500m` | `google/timesfm-2.0-500m-pytorch/torch_model.ckpt` | CPU | Same observation/horizon limits, frequency category 0/1/2 | Point predictions / CSV |
| `wan22_t2v_a14b` | complete T2V Diffusers package | NVIDIA + CPU offload | Prompt, dimensions divisible by 16, frames 4n+1 | MP4 |
| `wan22_i2v_a14b` | complete I2V Diffusers package | NVIDIA + CPU offload | Prompt plus first-frame image | MP4 |

Legacy GOT custom model code is not executed. Use the HF-native package. Inference
is forced offline after Drive materialization; the worker never receives OAuth tokens.
No fallback model, synthetic forecast, or fabricated OCR result is used.

## Dependency and hardware boundaries

Top-level versions are pinned in `runtime/managed_env.py`; transitive packages are
resolved by pip and checked with `pip check`. This is not a complete hash-locked
software supply chain. Setup remains dependent on python.org, PyPI and PyTorch's
wheel index. A first-run network failure is surfaced rather than marked successful.

The CPU worker reserves available RAM of 4 GiB (GOT), 3 GiB (Chronos), or 8 GiB
(TimesFM). These are provisional implementation gates, not measured guarantees.
TimesFM upstream recommends a 32 GB machine for dependencies. Wan A14B additionally
requires at least 32 GiB available RAM and 16 GiB NVIDIA VRAM for the offload route;
real device benchmarking is still required. It is not enabled on AMD Vega 8.

## Validation levels

1. **Source / contract tests:** input parsing, missing-file rejection, shard and path
   checks, credential boundaries, cache identities, HTTP routes, ranges, and failure
   handling. Tests with mocks must never be described as model inference tests.
2. **Browser DOM check:** separate offline rendering with mocked transport; verifies
   five selectors, correct inputs, missing-weight blocking, mobile overflow and JS
   errors. Live localhost navigation was blocked by this environment's browser policy.
3. **Windows package CI:** builds the standalone executable and verifies its routes.
4. **Real model/device acceptance:** requires user-authorized Drive weights and the
   intended host. Not established by source tests or successful executable startup.

## Remaining gates before declaring the whole project complete

- Run real chat, embedding and reranker with the prepared GGUFs, preserving returned
  text, vector dimensions and ranked scores as evidence.
- Run at least one real image and video on the supported NVIDIA host.
- Populate the native packages and run one real OCR, Chronos and TimesFM job.
- Exercise a two-device HTTPS + Runtime-token session including returned media.
- Validate interrupted downloads, resumed transfer and warm-cache reuse on Windows.
- Review and explicitly authorize merging the release PR; main is not modified by
  the implementation run.

`notebooks/Prepare_Models.ipynb` is a CPU-only preparation notebook. It pins each
selected Hugging Face repository to a resolved commit, verifies sizes and available
LFS SHA256s, and writes Drive manifests. Running it downloads weights; merely having
it in Drive or in the delivery ZIP does not make models ready.

## Source references for adapters

- GOT native API: https://huggingface.co/docs/transformers/v4.57.0/model_doc/got_ocr2
- Chronos-2 API: https://github.com/amazon-science/chronos-forecasting
- TimesFM 2.0 loader: https://github.com/google-research/timesfm/tree/master/v1
- TimesFM compatible package: https://pypi.org/project/timesfm/1.3.0/
- Wan2.2 Diffusers: https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B-Diffusers

## Run / verify

Normal Windows use: install the CI-produced package using `runtime/Install.cmd`.
Do not combine an older executable with newer source and call it the new release.
Development: `python -m runtime.application`.
Tests: `python -m unittest discover -s tests -v`.
Native endpoints: `GET /v1/native/catalog`, `POST /v1/native/plan|start|stop`,
`GET /v1/native/status|result|media`. Remote requests require the configured token.
