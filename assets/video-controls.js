(() => {
  "use strict";
  function validate(payload, adapter) {
    const result = { ...payload };
    for (const [field, min, max] of [["width", 256, 1920], ["height", 256, 1080], ["frames", 9, 241], ["fps", 1, 60], ["steps", 1, 100]]) {
      if (!Number.isInteger(result[field]) || result[field] < min || result[field] > max) throw new Error(`${field} 必须是 ${min}–${max} 的整数。`);
    }
    if (result.width % 16 || result.height % 16) throw new Error("视频宽高必须是 16 的倍数。");
    if ((result.frames - 1) % 4) throw new Error("视频帧数必须是 4n+1，例如 49、81、121。");
    if (!Number.isFinite(result.cfg) || result.cfg < 0 || result.cfg > 20) throw new Error("CFG 必须在 0–20。");
    if (result.seed !== undefined && (!Number.isSafeInteger(result.seed) || result.seed < 0)) throw new Error("Seed 必须是 0–9007199254740991 的整数。");
    if (typeof result.prompt !== "string" || !result.prompt.trim() || result.prompt.length > 12000) throw new Error("视频提示词不能为空，最长 12000 字符。");
    if (typeof result.negative_prompt !== "string" || result.negative_prompt.length > 6000) throw new Error("负面提示词最长 6000 字符。");
    if (adapter?.lockResolution && (result.width !== adapter.width || result.height !== adapter.height)) throw new Error("当前适配器使用固定分辨率。");
    return result;
  }
  function duration(frames, fps) {
    return Number.isInteger(frames) && frames >= 9 && frames <= 241 && (frames - 1) % 4 === 0 && Number.isInteger(fps) && fps >= 1 && fps <= 60
      ? "预计视频时长 " + (frames / fps).toFixed(2) + " 秒（不是生成耗时）"
      : "帧数须为 4n+1（9–241），FPS 为 1–60 的整数。";
  }
  async function fetchResult(runtimeFetch, jobId) {
    if (typeof jobId !== "string" || !/^[a-zA-Z0-9_-]+$/.test(jobId)) throw new Error("视频任务 ID 无效。");
    // Authorized fetch instead of a token-free <video src=runtime-url> request.
    const response = await runtimeFetch("/v1/video/file?job_id=" + encodeURIComponent(jobId), { cache: "no-store" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error ?? "视频结果读取失败：" + response.status);
    }
    const blob = await response.blob();
    if (!blob.size || !["video/mp4", "video/webm", "video/x-matroska", "image/gif"].includes(blob.type)) throw new Error("Runtime 没有返回有效的视频文件。");
    return blob;
  }
  window.ModelVideoControls = { validate, duration, fetchResult };
})();
