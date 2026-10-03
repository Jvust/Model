(() => {
  "use strict";
  const TYPE_LABELS = Object.freeze({
    all: "全部类型", chat: "聊天 / 代码", "image-generation": "图像生成",
    "image-edit": "图像编辑", "video-generation": "视频生成", vision: "视觉 / OCR",
    embedding: "向量 / 重排", timeseries: "时序预测", generic: "其他模型"
  });
  function filter(models, query = "", type = "all", filesOnly = false) {
    const terms = String(query).normalize("NFKC").toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    return (Array.isArray(models) ? models : []).filter(model => {
      if (!model || (filesOnly && model.vaultMissing)) return false;
      const workspace = Object.hasOwn(TYPE_LABELS, model.workspace) ? model.workspace : "generic";
      if (type !== "all" && workspace !== type) return false;
      const haystack = [model.name, model.id, model.category, model.workspace, model.backend,
        model.packagePath, model.relativePath, TYPE_LABELS[workspace]].filter(Boolean).join(" ")
        .normalize("NFKC").toLocaleLowerCase();
      return terms.every(term => haystack.includes(term));
    });
  }
  window.ModelLibraryControls = { filter, TYPE_LABELS };
})();
