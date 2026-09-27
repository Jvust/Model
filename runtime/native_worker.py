"""Isolated inference worker. No Drive credentials or remote model code are accepted."""
from __future__ import annotations

import base64
import csv
import io
import json
import math
import os
from pathlib import Path

CATALOG = {
    "got_ocr2": {"label": "GOT-OCR 2.0 (HF)", "kind": "ocr", "repo": "stepfun-ai/GOT-OCR-2.0-hf", "root": "ocr", "ram_gib": 4},
    "chronos_2": {"label": "Chronos-2", "kind": "forecast", "repo": "amazon/chronos-2", "root": "timeseries", "ram_gib": 3},
    "timesfm_2_0_500m": {"label": "TimesFM 2.0 500M", "kind": "forecast", "repo": "google/timesfm-2.0-500m-pytorch", "root": "timeseries", "ram_gib": 8},
    "wan22_t2v_a14b": {"label": "Wan2.2 T2V A14B (Diffusers)", "kind": "video", "repo": "Wan-AI/Wan2.2-T2V-A14B-Diffusers", "root": "video_ultra", "ram_gib": 32, "gpu": True},
    "wan22_i2v_a14b": {"label": "Wan2.2 I2V A14B (Diffusers)", "kind": "video", "repo": "Wan-AI/Wan2.2-I2V-A14B-Diffusers", "root": "video_ultra", "ram_gib": 32, "gpu": True},
}


def integer(value, minimum, maximum, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
        if float(value) != result:
            raise ValueError()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be {minimum}..{maximum}")
    return result


def series_values(payload):
    raw = payload.get("values")
    if raw is None:
        text = str(payload.get("csv") or "").strip()
        if not text or len(text.encode("utf-8")) > 1_000_000:
            raise ValueError("Provide numeric values or a CSV smaller than 1 MB")
        column = str(payload.get("column") or "").strip()
        if column:
            reader = csv.DictReader(io.StringIO(text))
            if column not in (reader.fieldnames or []):
                raise ValueError(f"CSV column not found: {column}")
            raw = [row[column] for row in reader]
        else:
            raw = text.replace(",", " ").split()
    if not isinstance(raw, list) or not 16 <= len(raw) <= 2048:
        raise ValueError("Provide 16..2048 observations; no silent truncation")
    values = []
    for value in raw:
        if isinstance(value, bool) or value is None:
            raise ValueError("Series must contain only finite numbers, with no missing values")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Series must contain only finite numbers") from exc
        if not math.isfinite(number):
            raise ValueError("Series cannot contain NaN or infinity")
        values.append(number)
    return values


def image_bytes(value):
    if not isinstance(value, str) or len(value) > 12_000_000:
        raise ValueError("Upload a PNG/JPEG/WebP image smaller than 8 MB")
    if value.startswith("data:"):
        header, _, value = value.partition(",")
        if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
            raise ValueError("Unsupported image type")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 image") from exc
    if not 1 <= len(raw) <= 8_000_000:
        raise ValueError("Image must be 1 byte..8 MB")
    return raw


def validate_input(model_id, payload):
    if model_id not in CATALOG or not isinstance(payload, dict):
        raise ValueError("Unknown native model or invalid task input")
    kind = CATALOG[model_id]["kind"]
    if kind == "ocr":
        image_bytes(payload.get("image"))
        return {"image": payload["image"], "max_new_tokens": integer(payload.get("max_new_tokens", 1024), 1, 4096, "max_new_tokens"), "format": bool(payload.get("format", False))}
    if kind == "forecast":
        return {"values": series_values(payload), "horizon": integer(payload.get("horizon", 24), 1, 256, "horizon"), "frequency": integer(payload.get("frequency", 0), 0, 2, "frequency")}
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt or len(prompt) > 6000:
        raise ValueError("Video prompt must contain 1..6000 characters")
    width = integer(payload.get("width", 832), 256, 1280, "width")
    height = integer(payload.get("height", 480), 256, 720, "height")
    frames = integer(payload.get("frames", 49), 5, 81, "frames")
    if width % 16 or height % 16 or frames % 4 != 1:
        raise ValueError("Video dimensions must be multiples of 16; frames must equal 4n+1")
    result = {"prompt": prompt, "negative_prompt": str(payload.get("negative_prompt") or "")[:6000], "width": width, "height": height, "frames": frames, "steps": integer(payload.get("steps", 30), 1, 80, "steps"), "seed": integer(payload.get("seed", 0), 0, 2**32 - 1, "seed")}
    if model_id == "wan22_i2v_a14b":
        image_bytes(payload.get("image"))
        result["image"] = payload["image"]
    return result


def load_image(encoded):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 25_000_000
    with Image.open(io.BytesIO(image_bytes(encoded))) as source:
        if source.width * source.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds 25 million pixels")
        return ImageOps.exif_transpose(source).convert("RGB")


def infer(model_id, model_dir, payload, output_dir):
    """Use only local materialized weights. Never substitute a fallback model."""
    import torch
    model_dir, output_dir = Path(model_dir), Path(output_dir)
    args = validate_input(model_id, payload)
    torch.set_num_threads(max(1, min(8, (os.cpu_count() or 4) - 1)))
    if model_id == "got_ocr2":
        from transformers import AutoProcessor, GotOcr2ForConditionalGeneration
        model = GotOcr2ForConditionalGeneration.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False, use_safetensors=True, torch_dtype=torch.float32).eval()
        processor = AutoProcessor.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
        inputs = processor(load_image(args["image"]), return_tensors="pt", format=args["format"])
        with torch.inference_mode():
            tokens = model.generate(**inputs, do_sample=False, max_new_tokens=args["max_new_tokens"])
        text = processor.decode(tokens[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        return {"kind": "ocr", "text": text, "model_id": model_id}
    if model_id == "chronos_2":
        import pandas as pd
        from chronos import Chronos2Pipeline
        pipeline = Chronos2Pipeline.from_pretrained(str(model_dir), device_map="cpu", local_files_only=True)
        frame = pd.DataFrame({"id": ["series"] * len(args["values"]), "timestamp": pd.date_range("2000-01-01", periods=len(args["values"]), freq="D"), "target": args["values"]})
        prediction = pipeline.predict_df(frame, prediction_length=args["horizon"], quantile_levels=[0.1, 0.5, 0.9], id_column="id", timestamp_column="timestamp", target="target")
        rows = [{"step": i + 1, "prediction": float(row["predictions"]), "q10": float(row["0.1"]), "q50": float(row["0.5"]), "q90": float(row["0.9"])} for i, (_, row) in enumerate(prediction.iterrows())]
        return {"kind": "forecast", "model_id": model_id, "rows": rows, "note": "Steps are relative to the submitted observations; quantiles are model estimates, not guarantees."}
    if model_id == "timesfm_2_0_500m":
        import numpy as np
        import timesfm
        model = timesfm.TimesFm(hparams=timesfm.TimesFmHparams(backend="cpu", per_core_batch_size=1, horizon_len=args["horizon"], context_len=2048, num_layers=50, use_positional_embedding=False), checkpoint=timesfm.TimesFmCheckpoint(path=str(model_dir / "torch_model.ckpt")))
        point, _ = model.forecast([np.asarray(args["values"], dtype=np.float32)], freq=[args["frequency"]])
        return {"kind": "forecast", "model_id": model_id, "rows": [{"step": i + 1, "prediction": float(value)} for i, value in enumerate(point[0, :args["horizon"]])], "note": "Point forecasts only; uncalibrated experimental quantiles are not presented as confidence intervals."}
    if not torch.cuda.is_available():
        raise RuntimeError("Wan2.2 requires an NVIDIA CUDA worker")
    from diffusers import WanPipeline, WanImageToVideoPipeline, AutoencoderKLWan
    from diffusers.utils import export_to_video
    cls = WanImageToVideoPipeline if model_id == "wan22_i2v_a14b" else WanPipeline
    vae = AutoencoderKLWan.from_pretrained(str(model_dir), subfolder="vae", torch_dtype=torch.float32, local_files_only=True)
    pipeline = cls.from_pretrained(str(model_dir), vae=vae, torch_dtype=torch.bfloat16, local_files_only=True)
    pipeline.enable_model_cpu_offload()
    pipeline.vae.enable_tiling()
    kwargs = {"prompt": args["prompt"], "negative_prompt": args["negative_prompt"], "width": args["width"], "height": args["height"], "num_frames": args["frames"], "num_inference_steps": args["steps"], "guidance_scale": 4.0, "guidance_scale_2": 3.0, "generator": torch.Generator(device="cpu").manual_seed(args["seed"])}
    if model_id == "wan22_i2v_a14b":
        kwargs["image"] = load_image(args["image"]).resize((args["width"], args["height"]))
    frames = pipeline(**kwargs).frames[0]
    export_to_video(frames, str(output_dir / "video.mp4"), fps=16)
    return {"kind": "video", "model_id": model_id, "file": "video.mp4", "fps": 16}


def main():
    import sys
    request = json.load(sys.stdin)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[name] = "1"
    result = infer(request["model_id"], request["model_dir"], request["input"], request["output_dir"])
    path = Path(request["output_dir"]) / "result.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    main()
