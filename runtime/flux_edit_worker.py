"""Curated FLUX.2 klein 4B Diffusers adapter (full local package only).

No remote model code, no fallback to a different model. On <12 GiB GPUs the
text encoder runs on CPU and the denoiser uses sequential CPU offload. This is
slower than the T4 staged path and requires at least 24 GiB available system RAM.
GPU execution remains an intended-host acceptance test, not a unit-test claim.
"""
from __future__ import annotations
import gc
from pathlib import Path


def run(model_dir, payload, output_dir, load_image):
    import torch
    from transformers import Qwen3ForCausalLM, Qwen2TokenizerFast
    from diffusers import (Flux2KleinPipeline, Flux2Transformer2DModel,
                           AutoencoderKLFlux2, FlowMatchEulerDiscreteScheduler)
    root = Path(model_dir)
    if not torch.cuda.is_available():
        raise RuntimeError('FLUX.2 图像编辑需要 NVIDIA CUDA Runtime')
    free, total = torch.cuda.mem_get_info()
    low_vram = total < 12 * 1024**3
    cc = torch.cuda.get_device_capability(0)
    dtype = torch.bfloat16 if cc[0] >= 8 else torch.float16
    if low_vram:
        import psutil
        if psutil.virtual_memory().available < 24 * 1024**3:
            raise RuntimeError('8GB 显存模式需要至少 24 GiB 当前可用 RAM；请关闭其他模型/程序或使用远程 GPU')
    print('FLUX stage A: text encoding; low_vram=', low_vram, flush=True)
    tokenizer = Qwen2TokenizerFast.from_pretrained(str(root / 'tokenizer'), local_files_only=True)
    text_encoder = Qwen3ForCausalLM.from_pretrained(
        str(root / 'text_encoder'), local_files_only=True, trust_remote_code=False,
        dtype=torch.float32 if low_vram else dtype, low_cpu_mem_usage=True,
        device_map={'': 'cpu' if low_vram else 0}, use_safetensors=True).eval()
    with torch.inference_mode():
        embeddings = Flux2KleinPipeline._get_qwen3_prompt_embeds(
            text_encoder=text_encoder, tokenizer=tokenizer, prompt=payload['prompt'],
            dtype=torch.float32 if low_vram else dtype,
            device=torch.device('cpu' if low_vram else 'cuda'), max_sequence_length=512,
            hidden_states_layers=(9, 18, 27)).detach().cpu()
    del text_encoder, tokenizer
    gc.collect()
    torch.cuda.empty_cache()
    print('FLUX stage B: transformer / VAE', flush=True)
    transformer = Flux2Transformer2DModel.from_pretrained(
        str(root / 'transformer'), local_files_only=True, dtype=dtype,
        low_cpu_mem_usage=True, use_safetensors=True, device_map={'': 'cpu' if low_vram else 0})
    vae = AutoencoderKLFlux2.from_pretrained(str(root / 'vae'), local_files_only=True,
        dtype=dtype, low_cpu_mem_usage=True, use_safetensors=True)
    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(str(root / 'scheduler'), local_files_only=True)
    pipe = Flux2KleinPipeline(scheduler=scheduler, vae=vae, text_encoder=None,
                             tokenizer=None, transformer=transformer, is_distilled=True)
    if low_vram:
        pipe.enable_sequential_cpu_offload()
    else:
        pipe.vae.to('cuda')
    pipe.vae.enable_tiling()
    pipe.vae.enable_slicing()
    # Keep PyTorch SDPA; do not force attention slicing over an optimized attention backend.
    references = [load_image(value) for value in payload.get('images', [])]
    # Cap reference pixels too: lowering only output resolution does not bound edit conditioning.
    for image in references:
        image.thumbnail((1024, 1024))
    embeddings = embeddings.to(device='cuda', dtype=dtype)
    with torch.inference_mode():
        output = pipe(image=references or None, prompt=None, prompt_embeds=embeddings,
            width=payload['width'], height=payload['height'], num_inference_steps=4,
            guidance_scale=1.0, num_images_per_prompt=1,
            generator=torch.Generator(device='cpu').manual_seed(payload['seed'])).images[0]
    target = Path(output_dir) / 'image.png'
    output.save(target)
    return {'kind': 'image', 'model_id': 'flux2_klein_4b_diffusers', 'file': 'image.png',
            'width': output.width, 'height': output.height, 'seed': payload['seed'],
            'steps': 4, 'guidance_scale': 1.0, 'low_vram_sequential_offload': low_vram}
