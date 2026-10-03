"""Isolated, offline Qwen 2511 worker for subject-preserving scene edits.

API source: https://huggingface.co/Qwen/Qwen-Image-Edit-2511
The caller selects a fixed scene preset, not an arbitrary generation prompt.
"""
from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

PRESERVE = (
    "Keep the original subject, face, hairstyle, clothing, pose, materials, "
    "art style, and composition unchanged. Do not remove clothing or add nudity. "
)
PRESETS: dict[str, str] = {
    "studio-background": PRESERVE + "Change only the background to a simple light studio backdrop.",
    "sunset-background": PRESERVE + "Change only the background to a beach at sunset.",
    "soft-lighting": PRESERVE + "Adjust only the lighting to soft, diffused daylight.",
}


def environment() -> dict:
    """Check the external inference environment without downloading weights."""
    try:
        import torch
        import diffusers
        import transformers
        import accelerate
        from PIL import Image
        from diffusers import QwenImageEditPlusPipeline

        supported = torch.cuda.is_available()
        return {
            "supported": supported,
            "torch": torch.__version__,
            "diffusers": diffusers.__version__,
            "cuda": torch.version.cuda,
            "detail": "编辑环境已检测到 CUDA" if supported else "未检测到可用 CUDA；请连接 NVIDIA 编辑环境。",
        }
    except (ImportError, OSError, RuntimeError) as error:
        return {
            "supported": False,
            "detail": "编辑依赖不可用，请配置 MODEL_DIFFUSERS_PYTHON 指向已安装 CUDA torch、diffusers、transformers、accelerate 和 Pillow 的 Python。",
            "error": str(error),
        }


def main() -> int:
    """Run a single bounded, offline edit and exit to release GPU memory."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--model-dir")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--operation", choices=list(PRESETS))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--steps", type=int, default=40)
    args = parser.parse_args()
    if not 0 <= args.seed <= 0xFFFFFFFF or not 1 <= args.steps <= 50:
        parser.error("seed must be 0..4294967295 and steps must be 1..50")
    if not args.preflight and not all((args.model_dir, args.input, args.output, args.operation)):
        parser.error("model-dir, input, output and operation are required")
    state = environment()
    if args.preflight:
        print(json.dumps(state, ensure_ascii=False), flush=True)
        return 0 if state["supported"] else 2
    if not state["supported"]:
        raise RuntimeError(state["detail"])

    import torch
    from PIL import Image, ImageOps
    from diffusers import QwenImageEditPlusPipeline

    # Validate the real decoder format and reject animation before loading any weights.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(args.input) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
                raise ValueError("参考图只支持单帧 PNG、JPEG 或 WebP。")
            if not 0 < source.width * source.height <= 12_000_000:
                raise ValueError("参考图超过 1200 万像素或尺寸无效。")
            source.verify()
        with Image.open(args.input) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    print("loading: 正在加载本地 2511 分片包", flush=True)
    pipe = QwenImageEditPlusPipeline.from_pretrained(
        args.model_dir, torch_dtype=torch.bfloat16, local_files_only=True
    )
    pipe.enable_model_cpu_offload()
    pipe.enable_attention_slicing()
    print("generating: 正在编辑参考图", flush=True)
    with torch.inference_mode():
        output = pipe(
            image=[image], prompt=PRESETS[args.operation],
            generator=torch.Generator(device="cpu").manual_seed(args.seed),
            true_cfg_scale=4.0, negative_prompt=" ",
            num_inference_steps=args.steps, num_images_per_prompt=1,
        ).images[0]
    target = Path(args.output)
    temporary = target.with_suffix(".partial")
    try:
        output.save(temporary, format="PNG")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print("complete: 编辑结果已保存", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
