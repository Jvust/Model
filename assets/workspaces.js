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
      description: "用独立 llama.cpp task server 做文本向量化或候选文档重排。",
      fields: [
        ["source", "文本 / 候选文档", "每行一条文本或候选文档…", "textarea"],
        ["query", "查询文本", "Reranker 必填；Embedding 可留空", "textarea"],
        ["topk", "返回数量", "5", "text"]
      ],
      backend: "llama.cpp"
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
        <p>从模型库选择模型进入对应工作区。2511 支持保留主体的背景和光线编辑；实际可用性以编辑环境检查为准。</p>
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
  let selectedEditModel = null;
  let imagePollTimer = null;
  let selectedTaskModel = null;
  let taskPollTimer = null;

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

  function stopTaskPolling() {
    if (taskPollTimer) {
      clearTimeout(taskPollTimer);
      taskPollTimer = null;
    }
  }

  function taskPhaseText(state) {
    const phase = String(state && state.phase || "idle");
    const labels = {
      idle: "等待任务模型",
      downloading: "正在从 Drive 准备专用 GGUF",
      loading: "正在启动 llama.cpp task server",
      ready: "专用任务模型已就绪",
      failed: "专用任务模型启动失败"
    };
    return (labels[phase] || phase) + (state && state.error ? "\n" + state.error : "");
  }

  async function taskStatus() {
    const response = await runtimeFetch("/v1/tasks/status", { cache: "no-store" });
    const state = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(state.error || "无法读取专用任务状态。");
    return state;
  }

  async function waitTaskReady(status, run, stop) {
    stopTaskPolling();
    const state = await taskStatus();
    let text = taskPhaseText(state);
    if (state.current_file) text += "\n文件：" + state.current_file;
    if (typeof state.download_progress === "number") {
      text += "\n缓存进度：" + Math.floor(state.download_progress * 100) + "%";
    }
    status.textContent = text;
    if (run) run.disabled = state.phase !== "ready";
    if (stop) stop.disabled = !state.running;

    if (state.phase === "ready") return state;
    if (state.phase === "failed") throw new Error(state.error || "专用任务模型启动失败。");
    if (state.phase === "idle") throw new Error("专用任务模型已停止。");

    return await new Promise((resolve, reject) => {
      taskPollTimer = setTimeout(() => {
        waitTaskReady(status, run, stop).then(resolve).catch(reject);
      }, 1000);
    });
  }

  async function ensureTaskModel(status, run, stop) {
    if (!selectedTaskModel) {
      throw new Error("请先从模型库选择 Embedding 或 Reranker 模型。");
    }
    const current = await taskStatus().catch(() => null);
    if (
      current &&
      current.ready &&
      current.adapter === selectedTaskModel.id
    ) {
      return current;
    }

    if (window.ModelApp && typeof window.ModelApp.syncRuntimeDriveSession === "function") {
      await window.ModelApp.syncRuntimeDriveSession();
    }

    status.textContent = "正在启动 " + selectedTaskModel.name + "…";
    if (run) run.disabled = true;
    if (stop) stop.disabled = false;

    const response = await runtimeFetch("/v1/tasks/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model_id: selectedTaskModel.id,
        name: selectedTaskModel.name,
        package_path: selectedTaskModel.packagePath || selectedTaskModel.relativePath || "",
        files: modelFiles(selectedTaskModel)
      })
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "专用任务模型启动失败：" + response.status);
    return await waitTaskReady(status, run, stop);
  }

  function formatEmbeddingResult(result) {
    const data = result && Array.isArray(result.data) ? result.data : [];
    if (!data.length) return JSON.stringify(result, null, 2).slice(0, 6000);
    const first = data[0] && Array.isArray(data[0].embedding) ? data[0].embedding : [];
    return [
      "向量数量：" + data.length,
      "向量维度：" + first.length,
      first.length ? "首条向量预览：" + first.slice(0, 12).map(v => Number(v).toFixed(5)).join(", ") : ""
    ].filter(Boolean).join("\n");
  }

  function formatRerankResult(result) {
    const rows =
      (result && Array.isArray(result.results) && result.results) ||
      (result && Array.isArray(result.data) && result.data) ||
      [];
    if (!rows.length) return JSON.stringify(result, null, 2).slice(0, 6000);
    return rows.slice(0, 20).map((row, index) => {
      const score =
        row.relevance_score ?? row.score ?? row.similarity ?? row.logit ?? "";
      const docIndex = row.index ?? row.document_index ?? index;
      return (index + 1) + ". 文档 " + docIndex + (score === "" ? "" : " · score=" + Number(score).toFixed(6));
    }).join("\n");
  }

  async function runTaskModel(form, status) {
    if (!selectedTaskModel) {
      status.textContent = "请先从模型库选择 Embedding 或 Reranker 模型。";
      return;
    }
    const values = new FormData(form);
    const run = form.querySelector('button[type="submit"]');
    const stop = form.querySelector("[data-stop-task]");
    try {
      await ensureTaskModel(status, run, stop);
      const kind = String(selectedTaskModel.taskKind || "");
      if (kind === "embedding") {
        const texts = String(values.get("source") || "")
          .split(/\r?\n/)
          .map(value => value.trim())
          .filter(Boolean);
        if (!texts.length) throw new Error("请至少输入一条要向量化的文本。");
        status.textContent = "正在生成向量…";
        const response = await runtimeFetch("/v1/tasks/embeddings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ input: texts })
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.error || "Embedding 执行失败。");
        status.textContent = formatEmbeddingResult(body.result || {});
      } else if (kind === "reranker") {
        const query = String(values.get("query") || "").trim();
        const documents = String(values.get("source") || "")
          .split(/\r?\n/)
          .map(value => value.trim())
          .filter(Boolean);
        if (!query) throw new Error("Reranker 需要查询文本。");
        if (!documents.length) throw new Error("请至少输入一条候选文档。");
        const topN = Math.max(1, Number(values.get("topk") || documents.length));
        status.textContent = "正在重排候选文档…";
        const response = await runtimeFetch("/v1/tasks/rerank", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query, documents, top_n: topN })
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.error || "Reranker 执行失败。");
        status.textContent = formatRerankResult(body.result || {});
      } else {
        throw new Error("未知专用任务类型。");
      }
    } catch (error) {
      status.textContent = "任务失败：\n" + String(error.message || error);
    } finally {
      if (run) run.disabled = false;
    }
  }

  async function stopTaskModel(status, run, stop) {
    stopTaskPolling();
    try {
      const response = await runtimeFetch("/v1/tasks/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || "停止专用任务模型失败。");
      status.textContent = "专用任务模型已停止。";
    } catch (error) {
      status.textContent = "停止失败：" + String(error.message || error);
    } finally {
      if (run) run.disabled = false;
      if (stop) stop.disabled = true;
    }
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
      building_workflow: "正在构建图像工作流",
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
    window.QwenImageEditor?.unmount();
    content.textContent = "";
    if (item.id === "image-edit" && selectedEditModel && window.QwenImageEditor) {
      window.QwenImageEditor.mount(content, selectedEditModel);
      return;
    }
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
    stop.textContent = "停止任务";
    stop.disabled = true;
    if (item.id === "image-generation") {
      stop.dataset.stopImage = "true";
      actions.appendChild(stop);
    } else if (item.id === "embedding-rag") {
      stop.dataset.stopTask = "true";
      actions.appendChild(stop);
    }
    form.appendChild(actions);
    form.addEventListener("submit", event => {
      event.preventDefault();
      const status = root.querySelector(".workspace-status");
      if (item.id === "image-generation") {
        runImageTask(form, status);
      } else if (item.id === "embedding-rag") {
        runTaskModel(form, status);
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
    } else if (item.id === "embedding-rag") {
      run.disabled = !selectedTaskModel;
      run.textContent = selectedTaskModel && selectedTaskModel.taskKind === "reranker"
        ? "启动并重排"
        : "启动并生成向量";
      stop.addEventListener("click", () => {
        const status = root.querySelector(".workspace-status");
        stopTaskModel(status, run, stop);
      });
      const source = form.querySelector('[name="source"]');
      const query = form.querySelector('[name="query"]');
      const topk = form.querySelector('[name="topk"]');
      if (selectedTaskModel && selectedTaskModel.taskKind === "embedding") {
        source.parentElement.querySelector("label").textContent = "文本（每行一条）";
        source.placeholder = "第一条文本\n第二条文本";
        query.parentElement.hidden = true;
        topk.parentElement.hidden = true;
      } else if (selectedTaskModel && selectedTaskModel.taskKind === "reranker") {
        source.parentElement.querySelector("label").textContent = "候选文档（每行一条）";
        source.placeholder = "候选文档 A\n候选文档 B\n候选文档 C";
        query.parentElement.hidden = false;
        topk.parentElement.hidden = false;
      }
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
      : item.id === "embedding-rag" && selectedTaskModel
        ? "当前模型 · " + selectedTaskModel.name + " · llama.cpp task server"
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
      : item.id === "embedding-rag"
        ? (
            selectedTaskModel
              ? "已选择 " + selectedTaskModel.name + " · " + selectedTaskModel.taskKind
              : "请先从模型库选择 Qwen3 Embedding 或 Reranker。"
          )
        : "点击“检查本机后端”获取 Runtime 检测结果。";
    check.addEventListener("click", async () => {
      status.textContent = "正在检查本机 Runtime…";
      try {
        const response = await runtimeFetch("/v1/backends", { cache: "no-store" });
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
      if (!model) return false;
      if (model.workspace === "image-generation") {
        selectedImageModel = model;
        active = "image-generation";
      } else if (model.id === "qwen_image_edit_2511" && model.workspace === "image-edit") {
        selectedEditModel = model;
        active = "image-edit";
      } else if (model.workspace === "embedding" && model.taskKind) {
        selectedTaskModel = model;
        active = "embedding-rag";
      } else {
        return false;
      }
      renderTabs();
      renderContent();
      root.scrollIntoView({ behavior: "smooth", block: "start" });
      return true;
    }
  };

  renderTabs();
  renderContent();
})();
