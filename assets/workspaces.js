(() => {
  "use strict";

  const workspaces = [
    {
      id: "image-generation",
      icon: "✦",
      title: "图像生成",
      eyebrow: "IMAGE · COMFYUI / DIFFUSERS",
      description: "用提示词生成图片，适合插画、产品概念图和风格探索。",
      fields: [
        ["prompt", "提示词", "描述主体、构图、光线和风格…", "textarea"],
        ["negative", "负面提示词", "不希望出现的内容（可选）", "textarea"],
        ["width", "宽度", "1024", "text"],
        ["height", "高度", "1024", "text"],
        ["steps", "Steps", "28", "text"],
        ["cfg", "CFG", "5", "text"],
        ["seed", "Seed", "留空为随机", "text"]
      ],
      backend: "ComfyUI"
    },
    {
      id: "image-edit",
      icon: "◌",
      title: "图像编辑",
      eyebrow: "IMAGE EDIT · INPAINT / UPSCALE",
      description: "围绕已有图片进行局部重绘、扩图和放大。",
      fields: [
        ["source", "图片地址或 Drive 文件 ID", "粘贴图片地址或文件 ID", "text"],
        ["instruction", "编辑指令", "例如：把背景换成夜景，保留主体…", "textarea"],
        ["strength", "编辑强度", "0.75", "text"]
      ],
      backend: "Diffusers"
    },
    {
      id: "vision-ocr",
      icon: "⌁",
      title: "视觉 / OCR",
      eyebrow: "VISION · TRANSFORMERS",
      description: "读取图片中的文字、表格和结构化信息，并进行问答。",
      fields: [
        ["source", "图片地址或 Drive 文件 ID", "粘贴图片地址或文件 ID", "text"],
        ["question", "识别任务", "例如：提取发票金额并输出 JSON…", "textarea"],
        ["language", "输出语言", "中文", "text"]
      ],
      backend: "Transformers"
    },
    {
      id: "embedding-rag",
      icon: "⌘",
      title: "向量检索",
      eyebrow: "RAG · EMBEDDING / RERANK",
      description: "把资料转成向量，建立可搜索的本地知识库。",
      fields: [
        ["source", "资料目录或 Drive 文件夹 ID", "粘贴目录或文件夹 ID", "text"],
        ["query", "检索问题", "输入要查找的问题…", "textarea"],
        ["topk", "返回数量", "5", "text"]
      ],
      backend: "Transformers"
    },
    {
      id: "timeseries",
      icon: "∿",
      title: "时序预测",
      eyebrow: "TIME SERIES · PYTORCH",
      description: "上传带时间列的数据，进行预测、异常检测和趋势分析。",
      fields: [
        ["source", "CSV 地址或 Drive 文件 ID", "粘贴 CSV 地址或文件 ID", "text"],
        ["target", "预测列", "例如：sales", "text"],
        ["horizon", "预测步数", "12", "text"]
      ],
      backend: "PyTorch"
    }
  ];

  const styles = `
    #workspaceHub { margin-top: 24px; }
    .workspace-shell { border: 1px solid var(--line); border-radius: 18px; overflow: hidden; background: linear-gradient(130deg,#191f31d9,#111727d0); }
    .workspace-head { padding: 22px; border-bottom: 1px solid var(--line); }
    .workspace-head h2 { margin: 5px 0 4px; font-size: 22px; }
    .workspace-head p { margin: 0; color: var(--muted); font-size: 12px; max-width: 760px; }
    .workspace-tabs { display: flex; gap: 8px; flex-wrap: wrap; padding: 14px 22px 0; }
    .workspace-tab { min-height: 36px; padding: 7px 12px; font-size: 11px; }
    .workspace-tab[aria-selected="true"] { color: #34263f; background: linear-gradient(115deg,#fbd3e4,#ccbcf3); border-color: #ffe5f099; }
    .workspace-content { padding: 18px 22px 22px; }
    .workspace-card { display: grid; grid-template-columns: minmax(0,1fr) minmax(250px,.65fr); gap: 18px; }
    .workspace-title { margin: 0; font-size: 19px; }
    .workspace-eyebrow { color: var(--pink); font-size: 10px; letter-spacing: 1.7px; }
    .workspace-description { color: var(--muted); font-size: 12px; margin: 7px 0 16px; }
    .workspace-form { display: grid; gap: 11px; }
    .workspace-field { display: grid; gap: 5px; }
    .workspace-field label { color: var(--muted); font-size: 10px; }
    .workspace-field input, .workspace-field textarea { width: 100%; min-height: 40px; padding: 8px 10px; color: var(--text); background: #0d1322; border: 1px solid #bfc4f02a; border-radius: 9px; outline: none; }
    .workspace-field textarea { min-height: 78px; resize: vertical; }
    .workspace-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 3px; }
    .workspace-side { padding: 16px; border: 1px solid var(--line); border-radius: 13px; background: #090e1770; }
    .workspace-side h3 { margin: 0 0 10px; font-size: 13px; }
    .workspace-status { min-height: 70px; color: var(--muted); font-size: 11px; white-space: pre-wrap; }
    .workspace-backend { color: var(--blue); font-size: 11px; margin-bottom: 12px; }
    .workspace-image-preview { width: 100%; max-height: 560px; object-fit: contain; border-radius: 10px; margin-top: 14px; border: 1px solid var(--line); background: #050912; }
    @media (max-width: 800px) { .workspace-card { grid-template-columns: 1fr; } }
  `;
  const style = document.createElement("style");
  style.textContent = styles;
  document.head.appendChild(style);

  const root = document.createElement("section");
  root.id = "workspaceHub";
  root.innerHTML = `
    <div class="workspace-shell">
      <div class="workspace-head">
        <div class="kicker">AI WORKSPACES · MULTI BACKEND</div>
        <h2>更多 AI 模型工作区</h2>
        <p>模型库会根据分类选择对应工作区。GGUF 聊天和视频工作区已经接通；下面的图像、视觉、检索和时序界面先统一好输入与运行方案。</p>
      </div>
      <div class="workspace-tabs" role="tablist" aria-label="AI 模型工作区"></div>
      <div class="workspace-content"></div>
    </div>`;
  const library = document.querySelector(".library-head");
  (library && library.parentElement ? library.parentElement : document.querySelector("main")).insertBefore(root, library || null);

  const tabs = root.querySelector(".workspace-tabs");
  const content = root.querySelector(".workspace-content");
  let active = workspaces[0].id;
  let backendState = null;
  let selectedImageModel = null;
  let imagePollTimer = null;

  function runtimeBase() {
    if (window.ModelApp && typeof window.ModelApp.runtimeBase === "function") {
      return String(window.ModelApp.runtimeBase() || "http://127.0.0.1:8765").replace(/\/$/, "");
    }
    return String(
      localStorage.getItem("model_runtime_base") ||
      (window.MODEL_CONFIG && window.MODEL_CONFIG.runtimeBase) ||
      "http://127.0.0.1:8765"
    ).replace(/\/$/, "");
  }

  async function runtimeFetch(path, options = {}) {
    if (
      window.ModelApp &&
      typeof window.ModelApp.runtimeFetch === "function"
    ) {
      return window.ModelApp.runtimeFetch(path, options);
    }
    return fetch(runtimeBase() + path, options);
  }

  function imageDefaults(model) {
    const hay = [
      model && model.id,
      model && model.name,
      model && model.repo,
      model && model.packagePath
    ].filter(Boolean).join(" ").toLowerCase();
    if (
      hay.includes("qwen_image_2_1_int8") ||
      hay.includes("qwen-image-2.1") ||
      hay.includes("qwen image 2.1")
    ) {
      return { width: 768, height: 768, steps: 20, cfg: 1.0 };
    }
    if (
      hay.includes("flux2_klein_4b_fp8") ||
      hay.includes("flux.2-klein-4b-fp8") ||
      hay.includes("flux2-klein-4b-fp8") ||
      hay.includes("flux2 klein 4b fp8")
    ) {
      return { width: 1024, height: 1024, steps: 4, cfg: 1.0 };
    }
    return { width: 1024, height: 1024, steps: 28, cfg: 5.0 };
  }

  function modelFiles(model) {
    return (Array.isArray(model && model.files) ? model.files : [])
      .map(file => ({
        drive_file_id: file.id,
        file_name: file.name,
        size: Number(file.size || 0) || null,
        md5_checksum: file.md5Checksum || file.md5_checksum || "",
        resource_key: file.resourceKey || file.resource_key || ""
      }))
      .filter(file => file.drive_file_id && file.file_name);
  }

  function imagePhaseText(state) {
    const phase = String(state && state.phase || "idle");
    const detail = String(state && state.detail || "");
    const labels = {
      idle: "等待任务",
      starting: "正在准备图像任务",
      preparing_comfyui: "正在准备 ComfyUI",
      downloading_model: "正在从 Drive 准备模型缓存",
      building_workflow: "正在构建 Pony SDXL 工作流",
      queued: "任务已提交",
      generating: "正在生成图像",
      complete: "图像生成完成",
      failed: "图像任务失败",
      cancelled: "图像任务已取消"
    };
    return (labels[phase] || phase) + (detail ? " · " + detail : "");
  }

  function stopImagePolling() {
    if (imagePollTimer) {
      clearTimeout(imagePollTimer);
      imagePollTimer = null;
    }
  }

  function imagePreview() {
    const side = root.querySelector(".workspace-side");
    if (!side) return null;
    let preview = side.querySelector(".workspace-image-preview");
    if (!preview) {
      preview = document.createElement("img");
      preview.className = "workspace-image-preview";
      preview.alt = "模型生成结果";
      preview.hidden = true;
      side.appendChild(preview);
    }
    return preview;
  }

  async function pollImage(status, form) {
    stopImagePolling();
    const run = form.querySelector('button[type="submit"]');
    const stop = form.querySelector("[data-stop-image]");
    try {
      const response = await runtimeFetch("/v1/image/status", { cache: "no-store" });
      const state = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(state.error || "无法读取图像任务状态。");
      let text = imagePhaseText(state);
      if (state.current_file) text += "\n文件：" + state.current_file;
      if (typeof state.download_progress === "number") {
        text += "\n缓存进度：" + Math.floor(state.download_progress * 100) + "%";
      }
      status.textContent = text;
      if (run) run.disabled = !!state.running;
      if (stop) stop.disabled = !state.running;

      if (state.phase === "complete" && state.job_id) {
        const preview = imagePreview();
        if (preview) {
          preview.src = runtimeBase() + "/v1/image/file?job_id=" + encodeURIComponent(state.job_id) + "&t=" + Date.now();
          preview.hidden = false;
        }
        if (run) run.disabled = false;
        if (stop) stop.disabled = true;
        return;
      }
      if (state.phase === "failed") {
        status.textContent = imagePhaseText(state) + (state.error ? "\n" + state.error : "");
        if (run) run.disabled = false;
        if (stop) stop.disabled = true;
        return;
      }
      if (state.phase === "cancelled") {
        if (run) run.disabled = false;
        if (stop) stop.disabled = true;
        return;
      }
      if (state.running) {
        imagePollTimer = setTimeout(() => pollImage(status, form), 1200);
      }
    } catch (error) {
      status.textContent = "无法读取图像任务：" + String(error.message || error);
      if (run) run.disabled = false;
      if (stop) stop.disabled = true;
    }
  }

  async function runImageTask(form, status) {
    if (!selectedImageModel) {
      status.textContent = "请先从模型库点击“使用图像模型”。";
      return;
    }
    const values = new FormData(form);
    const prompt = String(values.get("prompt") || "").trim();
    if (!prompt) {
      status.textContent = "请先填写图像提示词。";
      return;
    }
    const run = form.querySelector('button[type="submit"]');
    const stop = form.querySelector("[data-stop-image]");
    if (run) run.disabled = true;
    if (stop) stop.disabled = false;
    const preview = imagePreview();
    if (preview) preview.hidden = true;

    try {
      status.textContent = "正在同步 Drive 会话并检查 Runtime…";
      if (window.ModelApp && typeof window.ModelApp.syncRuntimeDriveSession === "function") {
        await window.ModelApp.syncRuntimeDriveSession();
      }
      const defaults = imageDefaults(selectedImageModel);
      const payload = {
        model_id: selectedImageModel.id,
        name: selectedImageModel.name,
        package_path: selectedImageModel.packagePath || selectedImageModel.relativePath || "",
        files: modelFiles(selectedImageModel),
        prompt,
        negative_prompt: String(values.get("negative") || "").trim(),
        width: Number(values.get("width") || defaults.width),
        height: Number(values.get("height") || defaults.height),
        steps: Number(values.get("steps") || defaults.steps),
        cfg: Number(values.get("cfg") || defaults.cfg)
      };
      const seed = String(values.get("seed") || "").trim();
      if (seed) payload.seed = Number(seed);
      const response = await runtimeFetch("/v1/image/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || "图像任务启动失败：" + response.status);
      await pollImage(status, form);
    } catch (error) {
      status.textContent = "图像任务启动失败：\n" + String(error.message || error);
      if (run) run.disabled = false;
      if (stop) stop.disabled = true;
    }
  }

  async function stopImageTask(form, status) {
    stopImagePolling();
    const run = form.querySelector('button[type="submit"]');
    const stop = form.querySelector("[data-stop-image]");
    try {
      const response = await runtimeFetch("/v1/image/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || "停止图像任务失败。");
      status.textContent = imagePhaseText(result.image || {});
    } catch (error) {
      status.textContent = "停止失败：" + String(error.message || error);
    } finally {
      if (run) run.disabled = false;
      if (stop) stop.disabled = true;
    }
  }

  function renderTabs() {
    tabs.textContent = "";
    workspaces.forEach(item => {
      const button = document.createElement("button");
      button.className = "workspace-tab";
      button.type = "button";
      button.role = "tab";
      button.dataset.workspace = item.id;
      button.setAttribute("aria-selected", String(item.id === active));
      button.textContent = item.icon + "  " + item.title;
      button.addEventListener("click", () => { active = item.id; renderTabs(); renderContent(); });
      tabs.appendChild(button);
    });
  }

  function renderContent() {
    const item = workspaces.find(entry => entry.id === active) || workspaces[0];
    content.textContent = "";
    const card = document.createElement("div");
    card.className = "workspace-card";

    const main = document.createElement("div");
    const eyebrow = document.createElement("div");
    eyebrow.className = "workspace-eyebrow";
    eyebrow.textContent = item.eyebrow;
    const title = document.createElement("h3");
    title.className = "workspace-title";
    title.textContent = item.title;
    const description = document.createElement("p");
    description.className = "workspace-description";
    description.textContent = item.description;
    const form = document.createElement("form");
    form.className = "workspace-form";

    item.fields.forEach(([id, label, placeholder, kind]) => {
      const field = document.createElement("div");
      field.className = "workspace-field";
      const labelEl = document.createElement("label");
      labelEl.textContent = label;
      labelEl.htmlFor = "workspace-" + id;
      const input = document.createElement(kind === "textarea" ? "textarea" : "input");
      input.id = "workspace-" + id;
      input.placeholder = placeholder;
      input.name = id;
      field.append(labelEl, input);
      form.appendChild(field);
    });

    if (item.id === "image-generation" && selectedImageModel) {
      const defaults = imageDefaults(selectedImageModel);
      const defaultsById = {
        width: defaults.width,
        height: defaults.height,
        steps: defaults.steps,
        cfg: defaults.cfg
      };
      Object.entries(defaultsById).forEach(([id, value]) => {
        const input = form.querySelector('[name="' + id + '"]');
        if (input) input.value = String(value);
      });
    }

    const actions = document.createElement("div");
    actions.className = "workspace-actions";
    const run = document.createElement("button");
    run.type = "submit";
    run.className = "primary";
    run.dataset.icon = "play";
    run.textContent = "提交工作区任务";
    const check = document.createElement("button");
    check.type = "button";
    check.textContent = "检查本机后端";
    actions.append(run, check);
    const stop = document.createElement("button");
    stop.type = "button";
    stop.dataset.stopImage = "true";
    stop.textContent = "停止任务";
    stop.disabled = true;
    if (item.id === "image-generation") actions.appendChild(stop);
    form.appendChild(actions);
    form.addEventListener("submit", event => {
      event.preventDefault();
      const status = root.querySelector(".workspace-status");
      if (item.id === "image-generation") {
        runImageTask(form, status);
      } else {
        status.textContent = item.title + " 的输入已准备好；该模型家族尚未完成真实 Runtime 适配，因此不会伪装成已执行。";
      }
    });
    if (item.id === "image-generation") {
      run.disabled = !selectedImageModel;
      stop.addEventListener("click", () => {
        const status = root.querySelector(".workspace-status");
        stopImageTask(form, status);
      });
    }
    main.append(eyebrow, title, description, form);

    const side = document.createElement("aside");
    side.className = "workspace-side";
    const sideTitle = document.createElement("h3");
    sideTitle.textContent = "运行方案";
    const backend = document.createElement("div");
    backend.className = "workspace-backend";
    backend.textContent = item.id === "image-generation" && selectedImageModel
      ? "当前模型 · " + selectedImageModel.name + " · " + item.backend
      : "目标后端 · " + item.backend;
    const status = document.createElement("div");
    status.className = "workspace-status";
    status.textContent = item.id === "image-generation"
      ? (selectedImageModel
          ? (
              "已选择 " + selectedImageModel.name +
              "。当前默认参数：" +
              imageDefaults(selectedImageModel).width + "×" +
              imageDefaults(selectedImageModel).height + " / " +
              imageDefaults(selectedImageModel).steps + " steps / CFG " +
              imageDefaults(selectedImageModel).cfg
            )
          : "请先从模型库选择一个已经接入适配器并通过硬件检查的图像模型。")
      : "点击“检查本机后端”获取 Runtime 检测结果。";
    check.addEventListener("click", async () => {
      status.textContent = "正在检查本机 Runtime…";
      try {
        const base = runtimeBase();
        const response = await fetch(base + "/v1/backends", { cache: "no-store" });
        if (!response.ok) throw new Error("HTTP " + response.status);
        backendState = await response.json();
        const info = backendState.backends && backendState.backends[item.backend];
        status.textContent = info
          ? ("后端：" + item.backend + "\n状态：" + (info.detected ? "已检测到" : "未检测到") + (info.detail ? "\n" + info.detail : ""))
          : ("Runtime 已连接，但尚未返回 " + item.backend + " 的检测信息。");
      } catch (error) {
        status.textContent = "无法连接本机 Runtime。请先安装并启动 Model Runtime。";
      }
    });
    side.append(sideTitle, backend, status);
    card.append(main, side);
    content.appendChild(card);
  }

  window.ModelWorkspaces = {
    openModel(model) {
      if (!model || model.workspace !== "image-generation") return false;
      selectedImageModel = model;
      active = "image-generation";
      renderTabs();
      renderContent();
      root.scrollIntoView({ behavior: "smooth", block: "start" });
      return true;
    }
  };

  renderTabs();
  renderContent();
})();