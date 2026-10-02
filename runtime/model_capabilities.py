from __future__ import annotations

import re
from typing import Any

# Adapter availability is separate from file presence. The selected Drive vault
# is scanned by the website; this audit snapshot only records what was missing
# from AI-Model-Vault on 2026-09-26.
MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "qwen_image_edit_2511": {"availability": "environment_required", "adapter": "Diffusers", "reason": "2511 参考图编辑已接入；需检查 CUDA 编辑环境并完成本地模型包准备。"},
    "qwen_image_2_1_int8": {"availability": "automatic", "adapter": "ComfyUI + ComfyUI-GGUF", "reason": "已接入 Qwen-Image 2.1 GGUF 三文件工作流；网页可链接 Drive 根目录现有模型文件夹，无需复制权重。"},
    "flux_1_dev": {"availability": "incomplete", "adapter": "ComfyUI/Diffusers", "reason": "Drive 缺少 FLUX.1-dev 主权重。"},
    "flux_1_kontext_dev": {"availability": "incomplete", "adapter": "ComfyUI/Diffusers", "reason": "Drive 缺少 FLUX.1-Kontext-dev 主权重或依赖。"},
    "flux_1_krea_dev": {"availability": "incomplete", "adapter": "ComfyUI/Diffusers", "reason": "Drive 缺少 FLUX.1-Krea-dev 主权重。"},
    "flux_1_fill_dev": {"availability": "incomplete", "adapter": "ComfyUI/Diffusers", "reason": "Drive 缺少 FLUX.1-Fill-dev 主权重。"},
    "flux_1_redux_dev": {"availability": "incomplete", "adapter": "ComfyUI/Diffusers", "reason": "Drive 缺少 Redux 图像编码器和 embedder。"},
    "sd35_large": {"availability": "incomplete", "adapter": "Diffusers", "reason": "Drive 缺少 Stable Diffusion 3.5 Large 主权重。"},
    "flux_nsfw_uncensored": {"availability": "dependency_blocked", "adapter": "ComfyUI/Diffusers", "reason": "依赖的 FLUX.1-dev 尚未完整。"},
    "pony_diffusion_v6_xl": {"availability": "automatic", "adapter": "ComfyUI", "reason": "已接入网页 Pony SDXL 图像工作流；启动前仍会检查本机 NVIDIA/ComfyUI 硬件条件。"},
    "flux2_klein_4b_fp8": {"availability": "automatic", "adapter": "ComfyUI", "reason": "FLUX.2 Klein 4B distilled 工作流已接通；需要主 FP8 权重、Qwen3-4B text encoder 和 FLUX2 VAE 三个固定文件齐全。"},
    "wan22_ti2v_5b": {"availability": "automatic", "adapter": "ComfyUI", "reason": "已接入网页视频工作区。"},
    "wan22_t2v_a14b": {"availability": "workflow_required", "adapter": "ComfyUI", "reason": "权重完整，但需要独立 T2V 工作流。"},
    "wan22_i2v_a14b": {"availability": "workflow_required", "adapter": "ComfyUI", "reason": "权重完整，但需要独立 I2V 工作流。"},
    "wan22_animate_14b": {"availability": "workflow_required", "adapter": "ComfyUI", "reason": "权重完整，但需要 Animate 工作流。"},
    "ltx_2_5": {"availability": "incomplete", "adapter": "Diffusers/ComfyUI", "reason": "Drive 目前没有 LTX-2.5 模型权重。"},
    "hunyuanvideo_1_5": {"availability": "automatic", "adapter": "ComfyUI", "reason": "已接入网页视频工作区。"},
    "qwen3_vl_8b_q6": {"availability": "adapter_required", "adapter": "llama.cpp", "reason": "GGUF 视觉模型需要 mmproj 和视觉输入适配。"},
    "qwen3_14b_q6": {"availability": "automatic", "adapter": "llama.cpp", "reason": "GGUF 聊天模型已接入。"},
    "deepseek_r1_distill_qwen14b_q6": {"availability": "automatic", "adapter": "llama.cpp", "reason": "GGUF 推理模型已接入。"},
    "qwen3_coder_30b_a3b_q5": {"availability": "automatic", "adapter": "llama.cpp", "reason": "GGUF 编程模型已接入。"},
    "got_ocr2": {"availability": "adapter_required", "adapter": "Transformers", "reason": "需要 OCR 预处理和 Transformers 运行适配。"},
    "qwen3_embedding_0_6b": {"availability": "automatic", "adapter": "llama.cpp task server", "reason": "已接入独立 Embedding task server；需要固定 Q8_0 GGUF 权重。"},
    "qwen3_reranker_0_6b": {"availability": "automatic", "adapter": "llama.cpp task server", "reason": "已接入独立 Reranker task server；需要固定 Q8_0 GGUF 权重。"},
    "chronos_2": {"availability": "adapter_required", "adapter": "PyTorch", "reason": "需要时间序列输入/输出适配。"},
    "timesfm_2_0_500m": {"availability": "adapter_required", "adapter": "PyTorch", "reason": "需要时间序列输入/输出适配。"},
    "webnovel_writer_27b_zh": {"availability": "automatic", "adapter": "llama.cpp", "reason": "GGUF 写作模型已接入。"},
    "creative_writing_4b_q8": {"availability": "automatic", "adapter": "llama.cpp", "reason": "GGUF 写作模型已接入。"},
}

_LINKED_MODEL_SOURCES = {"qwen_image_2_1_int8", "qwen_image_edit_2511"}

_MISSING_FROM_VAULT = {
    "qwen_image_2_1_int8",
    "qwen3_vl_8b_q6",
    "qwen3_14b_q6",
    "deepseek_r1_distill_qwen14b_q6",
    "qwen3_coder_30b_a3b_q5",
    "got_ocr2",
    "qwen3_embedding_0_6b",
    "qwen3_reranker_0_6b",
    "chronos_2",
    "timesfm_2_0_500m",
    "webnovel_writer_27b_zh",
    "creative_writing_4b_q8",
}

_STATUS_LABELS = {
    "environment_required": "待检查编辑环境",
    "automatic": "可直接使用",
    "adapter_required": "需要适配器",
    "workflow_required": "需要工作流",
    "incomplete": "Drive 文件不完整",
    "dependency_blocked": "依赖模型不完整",
    "unknown": "未登记",
}


def _key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def capability_for(model_id: str | None = None, name: str | None = None, category: str | None = None) -> dict[str, Any]:
    candidates = [_key(model_id), _key(name)]
    for candidate in candidates:
        if candidate and candidate in MODEL_CAPABILITIES:
            item = dict(MODEL_CAPABILITIES[candidate])
            item["model_id"] = candidate
            item["vault_snapshot"] = (
                "linked_folder"
                if candidate in _LINKED_MODEL_SOURCES
                else "not_in_vault"
                if candidate in _MISSING_FROM_VAULT
                else "folder_present"
            )
            item["label"] = _STATUS_LABELS.get(item["availability"], item["availability"])
            return item

    category_key = _key(category)
    fallback = {
        "llm": ("adapter_required", "llama.cpp", "需要确认 GGUF 文件和聊天适配。"),
        "reasoning": ("adapter_required", "llama.cpp", "需要确认 GGUF 文件和聊天适配。"),
        "code": ("adapter_required", "llama.cpp", "需要确认 GGUF 文件和聊天适配。"),
        "novel": ("adapter_required", "llama.cpp", "需要确认 GGUF 文件和聊天适配。"),
        "video": ("workflow_required", "ComfyUI", "需要对应的视频工作流。"),
        "image": ("adapter_required", "ComfyUI/Diffusers", "需要对应的图像工作流。"),
    }
    availability, adapter, reason = fallback.get(
        category_key,
        ("unknown", "model-specific", "没有匹配到模型能力登记。"),
    )
    return {
        "model_id": _key(model_id or name),
        "availability": availability,
        "adapter": adapter,
        "reason": reason,
        "label": _STATUS_LABELS[availability],
        "vault_snapshot": "unknown",
    }


def all_capabilities() -> list[dict[str, Any]]:
    return [
        {
            **dict(value),
            "model_id": key,
            "label": _STATUS_LABELS.get(value["availability"], value["availability"]),
            "vault_snapshot": (
                "linked_folder"
                if key in _LINKED_MODEL_SOURCES
                else "not_in_vault"
                if key in _MISSING_FROM_VAULT
                else "folder_present"
            ),
        }
        for key, value in MODEL_CAPABILITIES.items()
    ]
