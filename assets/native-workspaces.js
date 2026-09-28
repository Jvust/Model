(() => {
  "use strict";
  const root = document.createElement("section");
  root.id = "nativeWorkspaces";
  root.innerHTML = `<h2>OCR / 时序预测 / 完整视频模型包</h2>
    <p>直接使用 Drive 完整模型包。首次运行准备独立 Python 环境；不会修改系统 Python，也不会用其他模型代替缺失权重。</p>
    <label>模型 <select id="nativeModel"></select></label>
    <button id="nativeRefresh" type="button">读取已扫描模型</button>
    <div id="nativeInput"></div>
    <div class="model-actions"><button id="nativePlan" type="button">检查完整性与硬件</button>
    <button id="nativeRun" type="button" disabled>检查通过后运行</button>
    <button id="nativeStop" type="button" disabled>取消任务</button>
    <button id="nativeExport" type="button" disabled>导出结果</button></div>
    <pre id="nativeStatus" style="white-space:pre-wrap;overflow-wrap:anywhere">先连接 Drive 并扫描模型库。</pre>
    <video id="nativeVideo" controls hidden style="max-width:100%"></video>`;
  const style = document.createElement("style");
  style.textContent = `#nativeWorkspaces{padding:22px;border:1px solid var(--line);border-radius:18px;margin-top:24px}#nativeWorkspaces label{display:block;margin:12px 0 5px}#nativeWorkspaces input,#nativeWorkspaces select,#nativeWorkspaces textarea{box-sizing:border-box;max-width:100%;padding:9px;border:1px solid var(--line);border-radius:8px;background:#101725;color:var(--text)}#nativeWorkspaces select{width:100%}#nativeWorkspaces input{width:100%}#nativeWorkspaces .model-actions{margin-top:16px;gap:8px}`;
  document.head.append(style);
  document.querySelector("main").append(root);
  const $ = id => document.getElementById(id);
  let catalog = [], scanned = [], selected = null, result = null, job = null, timer = null;
  let generation = 0, videoUrl = null;
  const request = async (path, data) => {
    if (!window.ModelApp) throw new Error("Runtime 客户端尚未加载");
    const response = await window.ModelApp.runtimeFetch(path, data === undefined ? {cache: "no-store"} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  };
  const showError = error => { $("nativeStatus").textContent = String(error.message || error); };
  function manifest(model) {
    return {model_id: model.id, package_path: model.packagePath, manifest_files: (model.manifestFiles || model.files || []).map(file => ({drive_file_id: file.id, file_name: file.name, relative_path: file.relativePath, size: Number(file.size), md5_checksum: file.md5Checksum || "", resource_key: file.resourceKey || ""}))};
  }
  function selectModel() {
    selected = scanned.find(model => model.id === $("nativeModel").value) || null;
    $("nativeRun").disabled = true;
    const item = catalog.find(model => model.model_id === $("nativeModel").value);
    const target = $("nativeInput");
    target.textContent = "";
    if (!item) return;
    const field = (label, id, type, value) => {
      const wrap = document.createElement("label");
      wrap.style.display = "block";
      wrap.textContent = label + " ";
      const input = document.createElement(type === "textarea" ? "textarea" : "input");
      input.id = id;
      if (type !== "textarea") input.type = type;
      if (value !== undefined) input.value = value;
      if (type === "file") input.accept = "image/png,image/jpeg,image/webp";
      if (type === "textarea") {input.rows = 5; input.style.width = "100%";}
      wrap.append(input); target.append(wrap);
      return input;
    };
    if (item.kind === "forecast") {
      field("CSV 或 16–2048 个数字（逗号/空格/换行分隔）", "nativeSeries", "textarea", "");
      field("CSV 数值列名（纯数字时留空）", "nativeColumn", "text", "");
      field("预测步数（1–256）", "nativeHorizon", "number", "24");
      field("TimesFM 频率类别：0 高频 / 1 中频 / 2 低频", "nativeFreq", "number", "0");
    } else if (item.kind === "ocr") {
      field("待识别图片（最大 8 MB）", "nativeImage", "file");
      field("输出 token 上限", "nativeTokens", "number", "1024");
    } else {
      field("视频提示词", "nativePrompt", "textarea", "");
      field("步数", "nativeSteps", "number", "30");
      field("种子", "nativeSeed", "number", "0");
      if (item.model_id === "wan22_i2v_a14b") field("首帧图片", "nativeImage", "file");
    }
    $("nativeStatus").textContent = selected && !selected.vaultMissing ? "已读取模型包；先检查完整性与硬件。" : `Drive 缺少此模型包：${item.repo}。需要完整配置、分词器/处理器和权重，不能只创建空目录。`;
    $("nativePlan").disabled = !selected || selected.vaultMissing;
  }
  async function refresh() {
    const body = await request("/v1/native/catalog");
    catalog = body.models.filter(item => item.kind !== "image"); // Multi-image editing uses the unified activation workspace.
    const snapshot = window.DriveModelIndex.loadSnapshot();
    scanned = snapshot ? window.DriveModelIndex.flattenPackages(snapshot.tree, snapshot.registry) : [];
    $("nativeModel").textContent = "";
    catalog.forEach(item => {
      const option = document.createElement("option");
      option.value = item.model_id;
      const model = scanned.find(model => model.id === item.model_id && !model.vaultMissing);
      option.textContent = item.label + (model ? " · 已扫描到包" : " · 缺少权重");
      $("nativeModel").append(option);
    });
    selectModel();
  }
  async function checkPlan() {
    if (!selected) throw new Error("先扫描并选择模型");
    const plan = await request("/v1/native/plan", manifest(selected));
    $("nativeRun").disabled = !plan.ready;
    $("nativeStatus").textContent = plan.ready ? "包清单与硬件预检查通过；运行时还会检查分片、配置和真实加载结果。首次准备依赖需要网络。" : plan.reasons.join("\n");
    return plan;
  }
  async function fileData() {
    const file = $("nativeImage")?.files[0];
    if (!file || file.size > 8000000) throw new Error("请选择小于 8 MB 的图片");
    return new Promise((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file);});
  }
  async function poll(ticket) {
    if (ticket !== generation) return;
    try {
      const status = await request("/v1/native/status");
      if (ticket !== generation || status.job_id !== job) return;
      $("nativeStatus").textContent = [status.phase, status.detail, status.error].filter(Boolean).join("\n");
      if (status.phase === "complete") {
        result = await request("/v1/native/result?job_id=" + encodeURIComponent(job));
        $("nativeStatus").textContent = result.kind === "ocr" ? result.text || "没有识别到文字。" : JSON.stringify(result, null, 2);
        $("nativeExport").disabled = false;
        if (result.kind === "video") {
          const response = await window.ModelApp.runtimeFetch("/v1/native/media?job_id=" + encodeURIComponent(job));
          if (!response.ok) throw new Error("视频读取失败");
          if (videoUrl) URL.revokeObjectURL(videoUrl);
          videoUrl = URL.createObjectURL(await response.blob());
          $("nativeVideo").src = videoUrl; $("nativeVideo").hidden = false;
        }
      }
      if (["complete", "failed", "cancelled", "idle"].includes(status.phase)) {
        $("nativeStop").disabled = true; $("nativeRun").disabled = false;
        $("nativeModel").disabled = false; $("nativeRefresh").disabled = false; $("nativePlan").disabled = false; return;
      }
      timer = setTimeout(() => poll(ticket), 1200);
    } catch (error) {showError(error); $("nativeStop").disabled = false;}
  }
  async function run() {
    const info = catalog.find(item => item.model_id === selected?.id);
    if (!info) throw new Error("先选择模型");
    let input;
    if (info.kind === "ocr") input = {image: await fileData(), max_new_tokens: Number($("nativeTokens").value)};
    else if (info.kind === "forecast") input = {csv: $("nativeSeries").value, column: $("nativeColumn").value.trim(), horizon: Number($("nativeHorizon").value), frequency: Number($("nativeFreq").value)};
    else {input = {prompt: $("nativePrompt").value, steps: Number($("nativeSteps").value), seed: Number($("nativeSeed").value)}; if (info.model_id === "wan22_i2v_a14b") input.image = await fileData();}
    $("nativeRun").disabled = true;
    await window.ModelApp.syncRuntimeDriveSession();
    const status = await request("/v1/native/start", {...manifest(selected), input});
    job = status.job_id; result = null; generation += 1;
    $("nativeExport").disabled = true; $("nativeStop").disabled = false; $("nativeModel").disabled = true; $("nativeRefresh").disabled = true; $("nativePlan").disabled = true;
    $("nativeVideo").hidden = true; if (videoUrl) { URL.revokeObjectURL(videoUrl); videoUrl = null; }
    await poll(generation);
  }
  window.addEventListener("model-source-changed", () => {
    if (!$("nativeModel").disabled) refresh().catch(showError);
  });
  $("nativeRefresh").onclick = () => refresh().catch(showError);
  $("nativeModel").onchange = selectModel;
  $("nativePlan").onclick = () => checkPlan().catch(showError);
  $("nativeRun").onclick = () => run().catch(error => {showError(error); $("nativeRun").disabled = false;});
  $("nativeStop").onclick = () => request("/v1/native/stop", {}).then(() => {$("nativeStatus").textContent = "正在取消并释放进程…"; if (timer) clearTimeout(timer); poll(generation);}).catch(showError);
  $("nativeExport").onclick = () => {
    if (!result) return;
    const csv = result.kind === "forecast";
    const keys = csv ? Object.keys(result.rows[0]) : [];
    const text = csv ? [keys.join(","), ...result.rows.map(row => keys.map(key => Number(row[key])).join(","))].join("\n") : JSON.stringify(result, null, 2);
    const url = URL.createObjectURL(new Blob([text], {type: csv ? "text/csv;charset=utf-8" : "application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = `${job}.${csv ? "csv" : "json"}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  // Upgrade existing image/video panels to authenticated media reads. No tokens in URLs.
  const mediaSources = new WeakMap();
  const observer = new MutationObserver(records => records.forEach(({target}) => {
    if (!(target instanceof HTMLImageElement || target instanceof HTMLVideoElement)) return;
    const src = target.getAttribute("src") || "";
    if (!/^https?:/.test(src) || !window.ModelApp) return;
    const url = new URL(src), base = new URL(window.ModelApp.runtimeBase());
    if (url.origin !== base.origin || !["/v1/image/file", "/v1/video/file"].includes(url.pathname)) return;
    const previous = mediaSources.get(target);
    if (previous?.src === src) return;
    if (previous?.blob) URL.revokeObjectURL(previous.blob);
    const pending = {src, blob: null}; mediaSources.set(target, pending);
    window.ModelApp.runtimeFetch(url.pathname + url.search).then(response => {
      if (!response.ok) throw new Error("结果文件需要正确的 Runtime 令牌");
      return response.blob();
    }).then(blob => {
      if (mediaSources.get(target) !== pending) return;
      pending.blob = URL.createObjectURL(blob); target.src = pending.blob;
      if (target instanceof HTMLVideoElement) target.load();
    }).catch(showError);
  }));
  observer.observe(document.querySelector("main"), {subtree: true, attributes: true, attributeFilter: ["src"]});
  window.addEventListener("beforeunload", () => {generation++; if (timer) clearTimeout(timer); if (videoUrl) URL.revokeObjectURL(videoUrl); observer.disconnect();});
})();
