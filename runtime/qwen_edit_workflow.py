"""Fixed Qwen-Image-Edit-2511 GGUF graph; no browser-authored nodes/code.

Based on Comfy-Org TextEncodeQwenImageEditPlus and the official 2511 template.
This is a graph contract, not a claim of tested output on the user's GPU.
"""
from __future__ import annotations
import re

QWEN_EDIT_ADAPTER = {
    'label': 'Qwen-Image-Edit-2511 GGUF Q4_K_M',
    'match': ('qwen_image_edit_2511_gguf', 'qwen-image-edit-2511', 'qwen image edit 2511'),
    'workflow_kind': 'qwen_image_edit_2511', 'requires_comfy_gguf': True,
    'required_nodes': ('UnetLoaderGGUF', 'CLIPLoader', 'VAELoader', 'LoadImage', 'ImageScale',
                       'TextEncodeQwenImageEditPlus', 'ModelSamplingAuraFlow', 'CFGNorm',
                       'VAEEncode', 'KSampler', 'VAEDecode', 'SaveImage'),
    'required_node_values': {'CLIPLoader': {'type': 'qwen_image'}},
    'artifacts': {
        'unet': {'name': 'qwen-image-edit-2511-Q4_K_M.gguf', 'expected_bytes': 13244758624,
                 'directories': ('diffusion_models',)},
        'clip': {'name': 'qwen_2.5_vl_7b_fp8_scaled.safetensors', 'min_bytes': 7_000_000_000,
                 'directories': ('text_encoders',)},
        'vae': {'name': 'qwen_image_vae.safetensors', 'min_bytes': 200_000_000, 'directories': ('vae',)},
    },
    'min_vram_mb': 7500, 'min_ram_gib': 22, 'min_disk_free_gb': 26.0,
    'execution_verified': False,
    'defaults': {'width': 640, 'height': 960, 'steps': 28, 'cfg': 4.0,
                 'sampler_name': 'euler', 'scheduler': 'simple', 'size_step': 16, 'min_size': 256},
}


def build_qwen_edit(names, payload, job_id):
    from math import isfinite
    if not re.fullmatch('[a-f0-9]{32}', job_id):
        raise ValueError('Invalid image job ID')
    images = payload.get('_image_names') or []
    if not 1 <= len(images) <= 3 or any(not re.fullmatch(r'model_[a-f0-9]{32}_[0-2]\.(png|jpg|webp)', name) for name in images):
        raise ValueError('Qwen editing needs 1..3 validated image uploads')
    def integer(key, default):
        value = payload.get(key, default)
        if type(value) is not int:
            raise ValueError(key + ' must be an integer')
        return value
    width, height = integer('width', 640), integer('height', 960)
    if not (256 <= width <= 1536 and 256 <= height <= 1536) or width % 16 or height % 16:
        raise ValueError('Qwen image dimensions must be multiples of 16 in 256..1536')
    prompt = payload.get('prompt', '')
    negative = payload.get('negative_prompt', '')
    if not isinstance(prompt, str) or not isinstance(negative, str) or len(negative) > 6000:
        raise ValueError('Prompt fields must be strings, at most 6000 characters')
    prompt = prompt.strip()
    if not prompt or len(prompt) > 6000:
        raise ValueError('An editing instruction is required')
    cfg, steps, seed = payload.get('cfg', 4), integer('steps', 28), integer('seed', 0)
    if type(cfg) not in (int, float):
        raise ValueError('CFG must be a number')
    if not isfinite(cfg) or not 1 <= cfg <= 20 or not 1 <= steps <= 80 or not 0 <= seed < 2**53:
        raise ValueError('Invalid Qwen sampling parameters')
    graph = {
        '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': names['unet']}},
        '2': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': names['clip'], 'type': 'qwen_image', 'device': 'cpu'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': names['vae']}},
        '4': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['1', 0], 'shift': 3.1}},
        '5': {'class_type': 'CFGNorm', 'inputs': {'model': ['4', 0], 'strength': 1.0}},
    }
    conditioning = {'clip': ['2', 0], 'vae': ['3', 0]}
    for i, name in enumerate(images):
        load, resize = str(10 + 2*i), str(11 + 2*i)
        graph[load] = {'class_type': 'LoadImage', 'inputs': {'image': name}}
        graph[resize] = {'class_type': 'ImageScale', 'inputs': {'image': [load, 0], 'upscale_method': 'lanczos', 'width': width, 'height': height, 'crop': 'disabled'}}
        conditioning['image' + str(i+1)] = [resize, 0]
    graph.update({
        '20': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {**conditioning, 'prompt': prompt}},
        '21': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {**conditioning, 'prompt': negative}},
        '22': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['11', 0], 'vae': ['3', 0]}},
        '23': {'class_type': 'KSampler', 'inputs': {'model': ['5', 0], 'positive': ['20', 0], 'negative': ['21', 0],
                'latent_image': ['22', 0], 'seed': seed, 'steps': steps, 'cfg': cfg, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}},
        '24': {'class_type': 'VAEDecode', 'inputs': {'samples': ['23', 0], 'vae': ['3', 0]}},
        '25': {'class_type': 'SaveImage', 'inputs': {'images': ['24', 0], 'filename_prefix': 'image/qwen2511_' + job_id}},
    })
    return graph
