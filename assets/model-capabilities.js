(function () {
  "use strict";

  const states = {
    qwen_image_edit_2511: ["待检查编辑环境", "2511 完整 Diffusers 包已接入参考图编辑；需检查 CUDA 编辑环境并准备本地包，不能将文件存在等同于可运行。"],
    qwen_image_2_1_int8: ["可直接使用", "已接入 Qwen-Image 2.1 GGUF 三文件工作流；网页可链接 Drive 根目录现有模型文件夹，无需复制权重。"],
    flux_1_dev: ["Drive 文件不完整", "Drive 缺少 FLUX.1-dev 主权重。"],
    flux_1_kontext_dev: ["Drive 文件不完整", "Drive 缺少 FLUX.1-Kontext-dev 主权重或依赖。"],
    flux_1_krea_dev: ["Drive 文件不完整", "Drive 缺少 FLUX.1-Krea-dev 主权重。"],
    flux_1_fill_dev: ["Drive 文件不完整", "Drive 缺少 FLUX.1-Fill-dev 主权重。"],
    flux_1_redux_dev: ["Drive 文件不完整", "Drive 缺少 Redux 图像编码器和 embedder。"],
    sd35_large: ["Drive 文件不完整", "Drive 缺少 Stable Diffusion 3.5 Large 主权重。"],
    flux_nsfw_uncensored: ["依赖模型不完整", "依赖的 FLUX.1-dev 尚未完整。"],
    pony_diffusion_v6_xl: ["可直接使用", "已接入网页 Pony SDXL 图像工作流；启动前仍会检查本机 NVIDIA/ComfyUI 硬件条件。"],
    flux2_klein_4b_fp8: ["可直接使用", "FLUX.2 Klein 4B distilled 工作流已接通；需要主 FP8 权重、Qwen3-4B text encoder 和 FLUX2 VAE 三个固定文件齐全。"],
    wan22_ti2v_5b: ["可直接使用", "已接入网页视频工作区。"],
    wan22_t2v_a14b: ["需要工作流", "权重完整，但需要独立 T2V 工作流。"],
    wan22_i2v_a14b: ["需要工作流", "权重完整，但需要独立 I2V 工作流。"],
    wan22_animate_14b: ["需要工作流", "权重完整，但需要 Animate 工作流。"],
    ltx_2_5: ["Drive 文件不完整", "Drive 目前没有 LTX-2.5 模型权重。"],
    hunyuanvideo_1_5: ["可直接使用", "已接入网页视频工作区。"],
    qwen3_vl_8b_q6: ["需要适配器", "GGUF 视觉模型需要 mmproj 和视觉输入适配。"],
    qwen3_14b_q6: ["可直接使用", "GGUF 聊天模型已接入。"],
    deepseek_r1_distill_qwen14b_q6: ["可直接使用", "GGUF 推理模型已接入。"],
    qwen3_coder_30b_a3b_q5: ["可直接使用", "GGUF 编程模型已接入。"],
    got_ocr2: ["需要适配器", "需要 OCR 预处理和 Transformers 运行适配。"],
    qwen3_embedding_0_6b: ["可直接使用", "已接入独立 Embedding task server；需要固定 Q8_0 GGUF 权重。"],
    qwen3_reranker_0_6b: ["可直接使用", "已接入独立 Reranker task server；需要固定 Q8_0 GGUF 权重。"],
    chronos_2: ["需要适配器", "需要时间序列输入/输出适配。"],
    timesfm_2_0_500m: ["需要适配器", "需要时间序列输入/输出适配。"],
    webnovel_writer_27b_zh: ["可直接使用", "GGUF 写作模型已接入。"],
    creative_writing_4b_q8: ["可直接使用", "GGUF 写作模型已接入。"]
  };

  window.MODEL_CAPABILITIES = Object.fromEntries(
    Object.entries(states).map(([model_id, value]) => [
      model_id,
      {
        model_id,
        availability_label: value[0],
        availability_reason: value[1],
        label: value[0],
        reason: value[1]
      }
    ])
  );
})();
