(() => {
  "use strict";

  const PRESETS = {
    "studio-background": "浅色摄影棚背景",
    "sunset-background": "海边日落背景",
    "soft-lighting": "柔和自然光"
  };
  let dispose = null;

  function packagePayload(model) {
    if (!manifestState(model).ready) throw new Error("模型包清单不完整或包含越界、重复路径，请重新扫描。");
    return {
      package_path: model.packagePath,
      manifest_files: (model.manifestFiles ?? []).map(file => ({
        drive_file_id: file.id,
        file_name: file.name,
        relative_path: file.relativePath,
        size: Number(file.size ?? 0),
        md5_checksum: file.md5Checksum ?? null,
        resource_key: file.resourceKey ?? null
      }))
    };
  }

  function manifestState(model) {
    const prefix = String(model.packagePath ?? "").replace(/\/$/, "") + "/";
    const files = Array.isArray(model.manifestFiles) ? model.manifestFiles : [];
    const seen = new Set();
    const invalid = !model.packagePath || /[\\:]/.test(prefix) || prefix.split("/").some(part => part === ".." || part === ".") || files.some(file => {
      const path = String(file.relativePath ?? "");
      const key = path.toLowerCase();
      const bad = !path.startsWith(prefix) || /[\\:]/.test(path) || path.split("/").some(part => !part || part === "." || part === "..") || seen.has(key) || !file.id || !Number.isSafeInteger(Number(file.size)) || Number(file.size) < 0;
      seen.add(key);
      return bad;
    });
    const paths = files.map(file => String(file.relativePath ?? "").slice(prefix.length));
    const base = paths.includes("model_index.json") ? "" : "model/";
    const required = ["model_index.json", "transformer/config.json", "text_encoder/config.json", "vae/config.json", "tokenizer/tokenizer_config.json", "scheduler/scheduler_config.json", "processor/preprocessor_config.json"];
    const missing = required.filter(name => !paths.includes(base + name));
    if (invalid) missing.push("清单路径或文件信息无效");
    for (const role of ["transformer", "text_encoder", "vae"]) {
      if (!files.some(file => {
        const value = String(file.relativePath ?? "").slice(prefix.length);
        return value.startsWith(base + role + "/") && value.endsWith(".safetensors") && Number(file.size) > 0;
      })) missing.push(role + " 权重");
    }
    return { ready: missing.length === 0, missing };
  }

  function editPayload(model, operation, reference, seed) {
    if (model.id !== "qwen_image_edit_2511" || !Object.hasOwn(PRESETS, operation)) throw new Error("请选择 2511 和可用的场景预设。");
    const value = Number(seed);
    if ((typeof seed !== "number" && (typeof seed !== "string" || !/^\d+$/.test(seed.trim()))) || !Number.isInteger(value) || value < 0 || value > 0xffffffff) throw new Error("Seed 必须是 0–4294967295 的整数。");
    return { model_id: model.id, package_path: model.packagePath, operation, reference_image: reference, seed: value, steps: 40 };
  }

  function readReference(file) {
    if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type) || !file.size || file.size > 8 * 1024 * 1024) throw new Error("请选择不超过 8 MB 的非空 PNG、JPEG 或 WebP。");
    // https://developer.mozilla.org/en-US/docs/Web/API/FileReader/readAsDataURL
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("无法读取参考图。"));
      reader.readAsDataURL(file);
    });
  }

  async function request(path, payload = null) {
    if (!window.ModelApp?.runtimeFetch) throw new Error("Runtime 连接尚未准备好，请刷新网页。");
    const response = await window.ModelApp.runtimeFetch(path, payload ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    } : { cache: "no-store" });
    const state = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(response.status === 404 ? "请升级 Model Runtime 至 v0.17 或更新版本。" : state.error ?? "编辑请求失败。");
    return state;
  }

  function unmount() { dispose?.(); dispose = null; }

  function mount(container, model) {
    unmount();
    const card = document.createElement("section");
    card.className = "workspace-card edit-card";
    card.innerHTML = `
      <div class="edit-controls">
        <div class="workspace-eyebrow">QWEN IMAGE EDIT · 2511</div>
        <h3>参考图场景编辑</h3>
        <p>保留主体、衣着和画风，调整背景或光线。生成仍可能改变细节，请对照原图检查。</p>
        <p class="edit-package"></p>
        <form class="workspace-form">
          <label>参考图 <input name="reference" type="file" accept="image/png,image/jpeg,image/webp" required></label>
          <label>编辑内容 <select name="operation"></select></label>
          <label>Seed <input name="seed" type="number" min="0" max="4294967295" value="0" required></label>
          <div class="workspace-actions">
            <button type="button" data-environment>检查编辑环境</button>
            <button type="button" data-prepare disabled>准备 Drive 模型包</button>
            <button type="submit" class="primary" disabled>开始编辑</button>
            <button type="button" data-stop disabled>停止任务</button>
          </div>
        </form>
        <p class="workspace-status" role="status" aria-live="polite"></p>
        <progress class="edit-progress" max="1" value="0" aria-label="模型包准备进度" hidden></progress>
      </div>
      <div class="edit-comparison">
        <figure><figcaption>原图</figcaption><img data-source alt="所选参考图" hidden></figure>
        <figure><figcaption>编辑结果</figcaption><img data-result alt="2511 编辑结果" hidden><a data-download hidden download="qwen-2511-edit.png">下载 PNG</a></figure>
      </div>`;
    container.appendChild(card);
    const form = card.querySelector("form");
    const file = form.elements.reference;
    const operation = form.elements.operation;
    for (const [value, label] of Object.entries(PRESETS)) {
      const option = document.createElement("option"); option.value = value; option.textContent = label; operation.appendChild(option);
    }
    const check = card.querySelector("[data-environment]");
    const prepare = card.querySelector("[data-prepare]");
    const run = card.querySelector('[type="submit"]');
    const stop = card.querySelector("[data-stop]");
    const status = card.querySelector(".workspace-status");
    const progress = card.querySelector("progress");
    const source = card.querySelector("[data-source]");
    const result = card.querySelector("[data-result]");
    const download = card.querySelector("[data-download]");
    const manifest = manifestState(model);
    card.querySelector(".edit-package").textContent = manifest.ready
      ? model.name + " · 完整包清单已识别，尚未验证本机运行环境"
      : "缺少支持文件：" + manifest.missing.join("、") + "。请重新扫描完整 model 目录。";
    let disposed = false;
    let timer = null;
    let revision = 0;
    let refreshId = 0;
    let environmentReady = false;
    let packageReady = false;
    let action = "restoring";
    let connected = false;
    let packageState = {};
    let editState = {};
    let sourceUrl = null;
    let outputUrl = null;
    let shownJob = null;
    const alive = token => !disposed && token === revision;
    const ownEdit = () => editState.package_path === model.packagePath;
    const ownPackage = () => packageState.package_path === model.packagePath;
    const remoteBusy = () => editState.running === true || packageState.running === true;
    function controls() {
      const busy = Boolean(action) || remoteBusy();
      check.disabled = busy;
      prepare.disabled = busy || !connected || !environmentReady || !manifest.ready;
      run.disabled = busy || !connected || !environmentReady || !packageReady || !file.files?.length;
      file.disabled = busy;
      operation.disabled = busy;
      form.elements.seed.disabled = busy;
      stop.disabled = action === "stopping" || !(action === "reading" || action === "syncing" || (!action && ((ownEdit() && editState.running) || (ownPackage() && packageState.running))));
    }
    function begin(name) {
      revision += 1;
      refreshId += 1;
      if (timer) clearTimeout(timer);
      timer = null;
      action = name;
      controls();
      return revision;
    }
    function schedule(token) {
      if (!alive(token) || action || !remoteBusy()) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => restore(token), 1500);
    }
    async function showOutput(state, token, refresh) {
      if (!state.output_ready || !ownEdit() || state.job_id === shownJob) return;
      const response = await window.ModelApp.runtimeFetch("/v1/edit/file?job_id=" + encodeURIComponent(state.job_id), { cache: "no-store" });
      if (!response.ok) throw new Error("无法读取编辑结果；稍后重新检查可重试下载。");
      const blob = await response.blob();
      if (!alive(token) || refresh !== refreshId || state.job_id !== editState.job_id) return;
      if (blob.type && !blob.type.startsWith("image/png")) throw new Error("编辑结果类型无效。");
      if (outputUrl) URL.revokeObjectURL(outputUrl);
      outputUrl = URL.createObjectURL(blob);
      result.src = outputUrl; result.hidden = false;
      download.href = outputUrl; download.hidden = false; shownJob = state.job_id;
    }
    function renderState() {
      progress.hidden = !ownPackage() || (!packageState.running && packageState.phase !== "complete");
      progress.value = Math.max(0, Math.min(1, Number(packageState.download_progress) || 0));
      if (editState.running && !ownEdit()) {
        status.textContent = "Runtime 正在编辑另一个模型包，请等待该任务结束。";
      } else if (packageState.running && !ownPackage()) {
        status.textContent = "Runtime 正在准备另一个模型包，请等待该任务结束。";
      } else if (ownPackage() && packageState.running && !editState.running) {
        status.textContent = (packageState.detail || "正在准备模型包") + "\n" + (packageState.current_index || 0) + " / " + (packageState.file_count || 0) + " 文件";
      } else if (ownEdit() && editState.phase && editState.phase !== "idle") {
        status.textContent = ({starting:"正在启动编辑进程",loading:"正在加载 2511",generating:"正在编辑参考图",cancelling:"正在等待编辑进程退出",complete:"编辑完成，请对照原图检查",cancelled:"任务已停止",failed:"编辑失败"}[editState.phase] || editState.phase) + (editState.error ? "\n" + editState.error : "");
        if (editState.output_ready && !file.files?.length) status.textContent += "\n已恢复上次结果；原图未保存在网页中，请重新选择以便对比。";
      } else if (ownPackage() && ["failed", "cancelled"].includes(packageState.phase)) {
        status.textContent = packageState.error || "模型包准备已停止；可重新检查后重试。";
      } else {
        status.textContent = packageReady ? "已恢复本地模型包状态；检查编辑环境后可选择参考图。" : "先检查编辑环境；文件存在不代表模型已能运行。";
      }
    }
    async function restore(token = revision, keepMessage = false) {
      const refresh = ++refreshId;
      try {
        const [pkg, edit] = await Promise.all([request("/v1/packages/status"), request("/v1/edit/status")]);
        if (!alive(token) || refresh !== refreshId) return false;
        packageState = pkg; editState = edit; connected = true;
        packageReady = ownPackage() && pkg.phase === "complete" && !pkg.running;
        if (action === "restoring") action = null;
        if (!keepMessage) renderState();
        controls();
        await showOutput(edit, token, refresh);
        schedule(token);
        return true;
      } catch (error) {
        if (!alive(token) || refresh !== refreshId) return false;
        connected = false;
        packageReady = false;
        if (action === "restoring") action = null;
        status.textContent = "无法确认 Runtime 状态：" + String(error.message || error) + " 请重新检查环境后再操作。";
        controls();
        schedule(token);
        return false;
      }
    }
    async function fail(error, token) {
      if (!alive(token)) return;
      action = null;
      status.textContent = String(error.message || error);
      // An HTTP failure may occur after a command reached Runtime. Reconcile instead of assuming idle.
      await restore(token, true);
      if (alive(token)) controls();
    }
    file.addEventListener("change", () => {
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
      sourceUrl = null; source.hidden = true;
      result.hidden = true; download.hidden = true;
      shownJob = editState.job_id || null;
      if (file.files?.[0]) { sourceUrl = URL.createObjectURL(file.files[0]); source.src = sourceUrl; source.hidden = false; }
      controls();
    });
    check.addEventListener("click", async () => {
      if (action || remoteBusy()) return;
      const token = begin("checking");
      environmentReady = false;
      status.textContent = "正在检查 CUDA 与编辑依赖…";
      try {
        const state = await request("/v1/edit/environment");
        if (!alive(token)) return;
        if (!await restore(token, true)) throw new Error("无法确认模型包与任务状态，编辑环境未就绪。");
        if (!alive(token)) return;
        environmentReady = state.supported === true;
        action = null;
        status.textContent = state.detail || (environmentReady ? "编辑环境检查完成。" : "编辑环境尚未就绪。");
        controls(); schedule(token);
      } catch (error) { environmentReady = false; await fail(error, token); }
    });
    prepare.addEventListener("click", async () => {
      if (action || remoteBusy() || !connected || !environmentReady || !manifest.ready) return;
      const token = begin("syncing");
      status.textContent = "正在同步 Drive 授权；可停止本次准备。";
      try {
        await window.ModelApp.syncRuntimeDriveSession();
        if (!alive(token)) return;
        action = "preparing"; controls();
        await request("/v1/packages/materialize", packagePayload(model));
        if (!alive(token)) return;
        packageReady = false; action = null;
        await restore(token);
      } catch (error) { await fail(error, token); }
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (action || remoteBusy() || !connected || !environmentReady || !packageReady) return;
      const token = begin("reading");
      result.hidden = true; download.hidden = true;
      status.textContent = "正在读取参考图；可停止本次提交。";
      try {
        const reference = await readReference(file.files[0]);
        if (!alive(token)) return;
        const payload = editPayload(model, operation.value, reference, form.elements.seed.value);
        action = "submitting"; controls(); status.textContent = "正在提交编辑任务…";
        await request("/v1/edit/generate", payload);
        if (!alive(token)) return;
        shownJob = null; action = null;
        await restore(token);
      } catch (error) { await fail(error, token); }
    });
    stop.addEventListener("click", async () => {
      if (stop.disabled) return;
      if (action === "reading" || action === "syncing") {
        const token = begin(null);
        status.textContent = "已取消提交；未启动新的编辑或下载任务。";
        await restore(token, true);
        return;
      }
      const token = begin("stopping");
      status.textContent = "正在停止任务并等待退出…";
      try {
        if (ownEdit() && editState.running) {
          await request("/v1/edit/stop", { job_id: editState.job_id });
        } else if (ownPackage() && packageState.running) {
          const state = await request("/v1/packages/status");
          if (!alive(token)) return;
          if (state.package_path !== model.packagePath) throw new Error("不能停止另一个模型包的任务。");
          await request("/v1/packages/stop", { package_path: model.packagePath });
        }
        if (!alive(token)) return;
        action = null;
        await restore(token);
      } catch (error) { await fail(error, token); }
    });
    dispose = () => {
      disposed = true; revision += 1; refreshId += 1;
      if (timer) clearTimeout(timer);
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
      if (outputUrl) URL.revokeObjectURL(outputUrl);
    };
    status.textContent = "正在恢复 Runtime 模型包和编辑任务状态…";
    controls();
    void restore();
  }

  window.QwenImageEditor = { mount, unmount, manifestState, packagePayload, editPayload };
})();
