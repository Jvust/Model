(() => {
  "use strict";
  const root = document.createElement("section"); root.className = "panel"; root.id = "desktopSource";
  root.style.marginBottom = "24px";
  root.innerHTML = `<h2>模型来源</h2>
    <label for="modelSourceSelect">读取方式</label>
    <select id="modelSourceSelect"><option value="api">Google Drive API（原模式）</option><option value="desktop">Drive 桌面版目录（无需网页 OAuth）</option></select>
    <div id="desktopControls" hidden>
      <p>目录位于 Runtime 所在的 Windows 电脑。先在桌面版 Drive 登录，再选择 AI-Model-Vault 或按相同分类结构整理的模型目录。</p>
      <div class="model-actions"><button id="desktopPick" type="button">选择桌面版模型目录</button><button id="desktopScan" type="button">扫描目录</button><button id="desktopCancel" type="button" disabled>取消扫描</button></div>
      <p>只读原文件；选择模型后才读取完整文件到 Runtime 缓存，后续复用。流式文件可能需要联网并占用 Drive 自身缓存；不会声称“零下载”或“零磁盘”。</p>
      <pre id="desktopStatus" role="status" style="white-space:pre-wrap;overflow-wrap:anywhere">请选择目录。</pre>
    </div>`;
  document.querySelector(".control-grid").before(root);
  const $ = id => document.getElementById(id);
  let busy = false;
  const show = value => { $("desktopStatus").textContent = String(value); };
  const request = async (path, body) => {
    const response = await window.ModelApp.runtimeFetch(path, body === undefined ? {cache:"no-store"} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || "桌面版来源需要新版 Runtime 0.18");
    return value;
  };
  function lock(value) {
    busy = value;
    $("desktopPick").disabled = value;
    $("desktopScan").disabled = value;
    $("desktopCancel").disabled = !value;
    $("modelSourceSelect").disabled = value;
  }
  async function idle() {
    const state = await request("/v1/runtime");
    if ([state, state.task, state.native, state.package, state.image, state.video].some(item => item && (item.running || ["downloading","loading","starting","cancelling"].includes(item.phase)))) {
      throw new Error("请先停止当前模型及任务，再切换或扫描来源。");
    }
  }
  async function poll(selection = false) {
    $("desktopCancel").disabled = selection;
    for (;;) {
      const state = await request("/v1/desktop/status");
      show([state.label ? "已选择：" + state.label : "", state.phase,
        `${state.folders || 0} 文件夹 · ${state.files || 0} 文件`, state.error, state.configuration_error].filter(Boolean).join("\n"));
      if (!state.busy) {
        if (state.phase === "failed") throw new Error(state.error || "桌面扫描失败");
        if (!selection && state.phase === "complete" && state.snapshot_ready) {
          const snapshot = await request("/v1/desktop/snapshot");
          window.ModelApp.acceptDesktopSnapshot(snapshot);
          show(["扫描完成 · " + snapshot.files + " 文件（尚未读取权重内容）", ...(snapshot.warnings || [])].join("\n"));
        }
        return state;
      }
      await new Promise(resolve => setTimeout(resolve, 750));
    }
  }
  async function scan() {
    if (busy) return;
    lock(true);
    try { await idle(); window.ModelApp.useModelSource("desktop"); await request("/v1/desktop/scan", {}); await poll(); }
    catch(error) { show(error.message); }
    finally { lock(false); }
  }
  $("desktopPick").onclick = async () => {
    lock(true);
    try {
      await idle(); await request("/v1/desktop/pick", {});
      show("请在这台 Windows 电脑弹出的窗口中选择模型目录。");
      const state = await poll(true);
      if (state.phase === "configured") {
        await request("/v1/desktop/scan", {}); await poll();
      }
    } catch(error) { show(error.message); }
    finally { lock(false); }
  };
  $("desktopScan").onclick = scan;
  $("desktopCancel").onclick = () => request("/v1/desktop/stop", {}).then(()=>show("正在取消扫描；原文件未改动。原生选择窗口请点击窗口的取消按钮。")).catch(error=>show(error.message));
  $("modelSourceSelect").value = localStorage.getItem("model_source_mode") === "desktop" ? "desktop" : "api";
  $("desktopControls").hidden = $("modelSourceSelect").value !== "desktop";
  $("modelSourceSelect").onchange = async () => {
    const mode = $("modelSourceSelect").value;
    try { await idle(); window.ModelApp.useModelSource(mode); $("desktopControls").hidden = mode !== "desktop"; }
    catch(error) { $("modelSourceSelect").value = window.ModelApp.source(); $("desktopControls").hidden = false; show(error.message); }
  };
  window.ModelDesktop = {scan};
})();
