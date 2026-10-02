(() => {
  "use strict";

  const PRESETS = {
    "studio-background": "浅色摄影棚背景",
    "sunset-background": "海边日落背景",
    "soft-lighting": "柔和自然光"
  };
  let dispose = null;

  function packagePayload(model) {
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
    const paths = (model.manifestFiles ?? []).map(file => String(file.relativePath ?? "").slice(prefix.length));
    const base = paths.includes("model_index.json") ? "" : "model/";
    const required = ["model_index.json", "transformer/config.json", "text_encoder/config.json", "vae/config.json", "tokenizer/tokenizer_config.json", "scheduler/scheduler_config.json", "processor/preprocessor_config.json"];
    const missing = required.filter(name => !paths.includes(base + name));
    for (const role of ["transformer", "text_encoder", "vae"]) {
      if (!(model.manifestFiles ?? []).some(file => {
        const value = String(file.relativePath ?? "").slice(prefix.length);
        return value.startsWith(base + role + "/") && value.endsWith(".safetensors") && Number(file.size) > 0;
      })) missing.push(role + " 权重");
    }
    return { ready: missing.length === 0, missing };
  }

  function editPayload(model, operation, reference, seed) {
    if (model.id !== "qwen_image_edit_2511" || !Object.hasOwn(PRESETS, operation)) throw new Error("请选择 2511 和可用的场景预设。");
    const value = Number(seed);
    if (!Number.isInteger(value) || value < 0 || value > 0xffffffff) throw new Error("Seed 必须是 0–4294967295 的整数。");
    return { model_id: model.id, package_path: model.packagePath, operation, reference_image: reference, seed: value, steps: 40 };
  }

  function readReference(file) {
    if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 8 * 1024 * 1024) throw new Error("请选择不超过 8 MB 的 PNG、JPEG 或 WebP。");
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
    let environmentReady = false;
    let packageReady = false;
    let busy = false;
    let preparing = false;
    let sourceUrl = null;
    let outputUrl = null;
    let shownJob = null;
    function controls() {
      check.disabled = busy;
      prepare.disabled = busy || !environmentReady || !manifest.ready;
      run.disabled = busy || !environmentReady || !packageReady || !file.files?.length;
      stop.disabled = !busy;
    }
    function showError(error) { if (!disposed) { busy = false; status.textContent = String(error.message ?? error); controls(); } }
    async function poll() {
      try {
        if (disposed) return;
        if (preparing) {
          const state = await request("/v1/packages/status");
          if (disposed) return;
          if (state.package_path !== model.packagePath) throw new Error("Runtime 正在准备另一个模型包，请等待完成。");
          status.textContent = state.detail + "\n" + state.current_index + " / " + state.file_count + " 文件";
          progress.hidden = false;
          progress.value = state.download_progress ?? 0;
          if (state.phase === "complete") {
            packageReady = true; preparing = false; busy = false;
            status.textContent = "模型包已准备。选择参考图后可开始编辑。";
          } else if (["failed", "cancelled"].includes(state.phase)) throw new Error(state.error ?? "模型包准备已取消。");
        } else {
          const state = await request("/v1/edit/status");
          if (disposed) return;
          status.textContent = ({idle:"等待编辑任务",starting:"正在启动编辑进程",loading:"正在加载 2511",generating:"正在编辑参考图",complete:"编辑完成，请对照原图检查",cancelled:"任务已停止",failed:"编辑失败"}[state.phase] ?? state.phase) + (state.error ? "\n" + state.error : "");
          busy = state.running;
          if (state.output_ready && state.job_id !== shownJob) {
            const response = await window.ModelApp.runtimeFetch("/v1/edit/file?job_id=" + encodeURIComponent(state.job_id));
            if (!response.ok) throw new Error("无法读取编辑结果。");
            const blob = await response.blob();
            if (disposed) return;
            if (outputUrl) URL.revokeObjectURL(outputUrl);
            outputUrl = URL.createObjectURL(blob); result.src = outputUrl; result.hidden = false;
            download.href = outputUrl; download.hidden = false; shownJob = state.job_id;
          }
        }
        controls();
        if (busy && !disposed) timer = setTimeout(poll, 1500);
      } catch (error) { showError(error); }
    }
    file.addEventListener("change", () => {
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
      source.hidden = true;
      result.hidden = true; download.hidden = true;
      if (file.files?.[0]) { sourceUrl = URL.createObjectURL(file.files[0]); source.src = sourceUrl; source.hidden = false; }
      controls();
    });
    check.addEventListener("click", async () => {
      environmentReady = false;
      busy = true; controls(); status.textContent = "正在检查 CUDA 与编辑依赖…";
      try {
        const state = await request("/v1/edit/environment");
        if (disposed) return;
        environmentReady = state.supported === true;
        const pkg = await request("/v1/packages/status");
        if (disposed) return;
        packageReady = pkg.phase === "complete" && pkg.package_path === model.packagePath;
        status.textContent = state.detail ?? "编辑环境检查完成。";
        busy = false; controls();
      } catch (error) { showError(error); }
    });
    prepare.addEventListener("click", async () => {
      busy = true; controls();
      try {
        await window.ModelApp.syncRuntimeDriveSession();
        if (disposed) return;
        await request("/v1/packages/materialize", packagePayload(model));
        if (disposed) return;
        packageReady = false; preparing = true;
        await poll();
      } catch (error) { showError(error); }
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (busy || !environmentReady || !packageReady) return;
      busy = true; controls(); result.hidden = true; download.hidden = true;
      try {
        const reference = await readReference(file.files[0]);
        if (disposed) return;
        await request("/v1/edit/generate", editPayload(model, operation.value, reference, form.elements.seed.value));
        if (disposed) return;
        preparing = false; shownJob = null;
        await poll();
      } catch (error) { showError(error); }
    });
    stop.addEventListener("click", async () => {
      try {
        if (timer) clearTimeout(timer);
        if (preparing) {
          const state = await request("/v1/packages/status");
          if (state.package_path !== model.packagePath) throw new Error("不能停止另一个模型包的任务。");
        }
        await request(preparing ? "/v1/packages/stop" : "/v1/edit/stop", {});
        if (disposed) return;
        busy = false; preparing = false; status.textContent = "任务已停止。"; controls();
      } catch (error) { showError(error); }
    });
    dispose = () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      if (sourceUrl) URL.revokeObjectURL(sourceUrl);
      if (outputUrl) URL.revokeObjectURL(outputUrl);
    };
    status.textContent = "先检查编辑环境；文件存在不代表模型已能运行。";
    controls();
  }

  window.QwenImageEditor = { mount, unmount, manifestState, packagePayload, editPayload };
})();
