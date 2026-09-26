# Drive package manifests and local package materialization

## Why this exists

The original Drive scanner only retained large model-weight files. That was enough for single-file GGUF and fixed ComfyUI adapters, but it is insufficient for directory-based runtimes such as Diffusers, Transformers and many time-series packages.

For example, the existing Wan2.2 T2V/I2V packages in Drive contain complete Diffusers-style model directories:

- sharded `.safetensors` weights;
- `diffusion_pytorch_model.safetensors.index.json`;
- component `config.json` files;
- tokenizer / scheduler metadata;
- VAE and text-encoder assets.

If the scanner discards the small metadata files, a local worker cannot reconstruct a runnable package and would be forced to redownload the repository from the internet.

## Web index v3

The model index cache is now v3.

Weight extensions remain unchanged and continue to populate `model.files`.

The scanner additionally keeps lightweight package-support files:

- `.json`
- `.txt`
- `.yaml`
- `.yml`

Support files are capped at 16 MiB each and a vault-wide maximum count of 40,000. README/Markdown files are intentionally excluded because they are documentation, not runtime package metadata.

Every package now exposes:

- `files`: weight/model artifacts only;
- `supportFiles`: lightweight runtime metadata only;
- `manifestFiles`: ordered union of both;
- `fileCount`: model-weight file count;
- `supportFileCount`;
- `packageFileCount`.

Existing image/video/chat/task adapters continue using `files`, so this change does not reinterpret JSON as a model weight.

## Runtime package materializer

Runtime v0.16 adds an asynchronous package materializer.

Endpoints:

- `GET /v1/packages/status`
- `POST /v1/packages/materialize`
- `POST /v1/packages/stop`

Input:

- canonical `package_path`;
- `manifest_files` containing Drive ID, file name, relative path, size and optional checksum/resource key.

The materializer:

1. validates package and relative paths against traversal;
2. validates duplicate file IDs / duplicate local paths;
3. reuses the existing resumable `DriveCache`;
4. restores the original package-relative directory structure under `D:\Model\packages`;
5. hard-links cached files into the package view when possible;
6. refuses to silently copy a large file if hard-linking is unavailable, avoiding a second 10–100 GB local copy;
7. writes `.jvust-package.json` after successful materialization.

Small files up to 64 MiB may use a copy fallback if a hard link is unavailable.

## Security / integrity boundary

- Drive OAuth token remains memory-only.
- A Drive file ID is never used as a path.
- `package_path` and every manifest relative path reject absolute paths, `..` traversal and Windows drive-colon components.
- Existing materialized files are only reused when they are the same physical file as the current DriveCache entry. A changed Drive file ID with the same name/size is relinked.
- Package materialization only reconstructs files; it does not import Python code or execute the package.

## Wan2.2 implication

The audited Drive copies of Wan2.2 T2V-A14B and I2V-A14B are complete Diffusers-style packages, not ComfyUI single-file UNET artifacts.

Their high/low-noise components are six-shard safetensor directories plus index/config metadata. Therefore the correct next step is a Drive-first Diffusers video worker consuming the materialized package.

Do **not** feed those shards directly into ComfyUI `UNETLoader`, and do not let the current ComfyUI workflow download a duplicate model repository.

## Next execution layer

The next video milestone should:

1. materialize the selected Wan2.2 T2V/I2V Drive package;
2. prepare a managed Diffusers/PyTorch worker only when needed;
3. load the reconstructed local package without network model downloads;
4. keep the existing web prompt/progress/result contract;
5. use the remote NVIDIA Runtime when the client has no supported NVIDIA GPU.
