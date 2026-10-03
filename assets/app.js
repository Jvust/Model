(function () {
  "use strict";

  let accessToken = null;
  let models = [];
  let runtimeBase = "";
  let runtimeToken = "";
  let runtimeState = null;
  let backendState = null;
  let modelCapabilities = window.MODEL_CAPABILITIES || {};
  let runtimePollTimer = null;
  let runtimeReconnectTimer = null;
  let chatBusy = false;
  let chatHistory = [];
  let selectedVideoModel = null;
  let videoPollTimer = null;
  let videoOutputUrl = null;
  let videoViewEpoch = 0;
  let videoPollEpoch = -1;
  let videoCommandBusy = false;
  let videoLastJobId = null;
  let videoReturnFocus = null;
  let scanBusy = false;

  const LINKED_MODEL_FOLDERS = [
    {
      modelId: "qwen_image_edit_2511",
      folderName: "Qwen-Image-Edit-2511",
      categoryRoot: "image_edit",
      parentId: null
    },
    {
      modelId: "qwen_image_2_1_int8",
      folderName: "Qwen-Image-2.1-GGUF",
      categoryRoot: "image_base"
    }
  ];

  const CHAT_BOOTSTRAP = {
    folderName: "notebook_launchers",
    fileName: "启动_Qwen3-0.6B_Q8_0_DriveFirst.ipynb",
    modelName: "Qwen3-0.6B Q8_0",
    approxSize: "约 639 MB"
  };

  const FLUX2_BOOTSTRAP = {
    folderName: "notebook_launchers",
    fileName: "启动_FLUX2-klein-4b-fp8_补齐依赖_DriveFirst.ipynb",
    modelName: "FLUX.2 Klein 4B FP8"
  };

  const TASK_BOOTSTRAP = {
    folderName: "notebook_launchers",
    fileName: "启动_Qwen3_Embedding_Reranker_0.6B_Q8_0_DriveFirst.ipynb",
    modelName: "Qwen3 Embedding / Reranker 0.6B Q8_0"
  };

  const $ = id => document.getElementById(id);

  function setStatus(text) {
    $("status").textContent = text;
  }

  function showError(error) {
    $("error").textContent = error ? String(error.message || error) : "";
  }

  function setDriveState(text) {
    $("driveState").textContent = text;
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "大小未知";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = bytes;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    return size.toFixed(index >= 3 ? 2 : 1) + " " + units[index];
  }

  function extension(name) {
    return window.DriveModelIndex.extensionOf(name).replace(".", "").toUpperCase() || "FILE";
  }

  function normalizeRuntimeBase(value) {
    const raw = String(value || "").trim().replace(/\/$/, "");
    const parsed = new URL(raw);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      throw new Error("Runtime 地址只支持 http:// 或 https://。");
    }
    const host = String(parsed.hostname || "").toLowerCase();
    const loopback = ["127.0.0.1", "localhost", "::1"].includes(host);
    if (!loopback && parsed.protocol !== "https:") {
      throw new Error("远程 Runtime 必须使用 HTTPS；推荐 Tailscale Serve。");
    }
    return raw;
  }

  function runtimeUrl(path) {
    return runtimeBase.replace(/\/$/, "") + path;
  }

  function runtimeHeaders(headers = {}) {
    const out = new Headers(headers || {});
    if (runtimeToken) out.set("Authorization", "Bearer " + runtimeToken);
    return out;
  }

  async function runtimeFetch(path, options = {}) {
    const request = { ...options, headers: runtimeHeaders(options.headers) };
    return fetch(runtimeUrl(path), request);
  }

  async function syncRuntimeDriveSession() {
    if (!accessToken) {
      await ensureAccessToken();
    }
    const response = await runtimeFetch("/v1/drive/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: accessToken })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "无法把 Drive 会话同步到本机 Runtime。");
    }
    return true;
  }

  function clearRuntimePoll() {
    if (runtimePollTimer) {
      clearTimeout(runtimePollTimer);
      runtimePollTimer = null;
    }
  }

  function clearRuntimeReconnect() {
    if (runtimeReconnectTimer) {
      clearTimeout(runtimeReconnectTimer);
      runtimeReconnectTimer = null;
    }
  }

  function scheduleRuntimeReconnect(delay = 3000) {
    clearRuntimeReconnect();
    runtimeReconnectTimer = setTimeout(async () => {
      const ok = await refreshRuntime();
      if (!ok) scheduleRuntimeReconnect(Math.min(delay + 1000, 10000));
    }, delay);
  }

  function setRuntimeInstalledState(connected) {
    const install = $("installRuntimeBtn");
    const hint = $("runtimeHint");
    if (install) install.hidden = connected;
    if (hint) {
      const remote = runtimeBase && !/^http:\/\/(127\.0\.0\.1|localhost|\[?::1\]?)(:|\/|$)/i.test(runtimeBase);
      hint.textContent = connected
        ? (remote
            ? "远程 NVIDIA Runtime 已连接；模型任务会通过私有 HTTPS Runtime 执行。"
            : "本机 AI 引擎已自动连接。以后直接打开这个网页即可。")
        : (remote
            ? "未连接远程 Runtime。确认 Tailscale、HTTPS 地址和 Runtime 令牌后重试。"
            : "未检测到本机 AI 引擎。首次安装一次后，以后只需要打开网页。");
    }
  }

  function scheduleRuntimeRefresh() {
    clearRuntimePoll();
    if (
      runtimeState &&
      (runtimeState.phase === "loading" || runtimeState.phase === "downloading")
    ) {
      runtimePollTimer = setTimeout(() => {
        refreshRuntime().catch(() => {});
      }, 1000);
    }
  }

  function runtimeLabel(state) {
    if (!state) return "未连接本地 Runtime";
    if (state.phase === "downloading") {
      const pct =
        typeof state.download_progress === "number"
          ? Math.floor(state.download_progress * 100)
          : null;
      return (
        "Drive 下载中 · " +
        (state.model || "GGUF") +
        (pct === null ? "" : " · " + pct + "%")
      );
    }
    if (state.running && state.ready) {
      return "已就绪 · " + (state.model || "GGUF");
    }
    if (state.running && state.phase === "loading") {
      return "加载中 · " + (state.model || "GGUF");
    }
    if (state.phase === "failed") return "Runtime 异常";
    if (state.running) return "运行中 · " + (state.model || "GGUF");
    return "已连接 · 当前空闲";
  }

  function runtimeLogText(state) {
    const lines = [];
    const hardware = state && state.hardware;
    if (hardware) {
      const gpu = hardware.gpu || {};
      const memory = hardware.memory || {};
      const disk = hardware.cache_disk || {};
      lines.push(
        "[hardware] " + (
          gpu.detected
            ? (gpu.detail || "NVIDIA GPU detected")
            : "未检测到 NVIDIA GPU · 图像/视频需要远程 NVIDIA Runtime"
        )
      );
      if (memory.available_gb != null) {
        lines.push("[memory] " + Number(memory.available_gb).toFixed(1) + " GB available");
      }
      if (disk.free_gb != null) {
        lines.push("[cache] " + Number(disk.free_gb).toFixed(1) + " GB free");
      }
    }
    if (state && state.last_error) lines.push("[bridge] " + state.last_error);
    if (state && state.logs && state.logs.length) {
      lines.push(...state.logs.slice(-12));
    }
    return lines.length ? lines.join("\n") : "Runtime 已连接，暂无日志。";
  }

  function updateDriveRuntimeCompatibility() {
    if (!runtimeState) return;
    if (runtimeState.drive_api_session) {
      setDriveState("Drive API 已连接 · Runtime 使用本机缓存，不需要 Google Drive 桌面版。");
    }
  }

  function updateChatAvailability() {
    const ready = !!(runtimeState && runtimeState.ready);
    $("chatInput").disabled = !ready || chatBusy;
    $("sendBtn").disabled = !ready || chatBusy;
    $("chatModelLabel").textContent = ready
      ? "当前模型 · " + (runtimeState.model || "GGUF")
      : runtimeState && runtimeState.phase === "loading"
        ? "模型加载中，完成后即可聊天。"
        : "先从下方模型库启动一个 GGUF。";
  }

  async function refreshRuntime() {
    clearRuntimePoll();
    try {
      const response = await runtimeFetch("/v1/runtime", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      runtimeState = await response.json();
      try {
        const backendResponse = await runtimeFetch("/v1/backends", { cache: "no-store" });
        backendState = backendResponse.ok ? await backendResponse.json() : null;
      } catch (_) {
        backendState = null;
      }
      await loadModelCapabilities();
      if (accessToken) {
        try {
          await syncRuntimeDriveSession();
          runtimeState.drive_api_session = true;
        } catch (_) {}
      }
      clearRuntimeReconnect();
      setRuntimeInstalledState(true);
      $("runtimeState").textContent = runtimeLabel(runtimeState);
      $("runtimeLog").textContent = runtimeLogText(runtimeState);
      $("stopBtn").disabled = !runtimeState.running;
      updateDriveRuntimeCompatibility();
      updateChatAvailability();
      renderModels();
      if (
        runtimeState.video &&
        !$("videoWorkspace").hidden &&
        runtimeState.video.running
      ) {
        setVideoStatus(
          videoPhaseText(runtimeState.video),
          runtimeState.video.download_progress
        );
      }
      scheduleRuntimeRefresh();
      return true;
    } catch (_) {
      runtimeState = null;
      backendState = null;
      $("runtimeState").textContent = "未检测到本机 AI 引擎";
      $("runtimeLog").textContent =
        "网页会持续自动重连。若这是第一次使用，请先安装一次 Model Runtime。";
      $("stopBtn").disabled = true;
      setRuntimeInstalledState(false);
      updateChatAvailability();
      return false;
    }
  }

  function saveRuntimeBase() {
    runtimeBase = normalizeRuntimeBase(
      $("runtimeUrl").value.trim() || window.MODEL_CONFIG.runtimeBase
    );
    runtimeToken = String($("runtimeToken").value || "").trim();
    localStorage.setItem("model_runtime_base", runtimeBase);
    if (runtimeToken) {
      sessionStorage.setItem("model_runtime_token", runtimeToken);
    } else {
      sessionStorage.removeItem("model_runtime_token");
    }
    $("runtimeUrl").value = runtimeBase;
  }

  function saveDriveSelection(folder) {
    if (!folder || !folder.id) return;
    $("folderId").value = folder.id;
    localStorage.setItem("model_drive_root_id", folder.id);
    localStorage.setItem("model_drive_root_name", folder.name || "");
    setDriveState("已选择 Drive 文件夹 · " + (folder.name || folder.id));
  }

  async function ensureAccessToken() {
    if (!accessToken) {
      accessToken = await window.DriveModelClient.getAccessToken();
    }
    if (!accessToken) {
      throw new Error("尚未获得 Google Drive 授权。请先点击“连接 Google Drive”。");
    }
    return accessToken;
  }

  async function findPreferredFolder(options = {}) {
    const { quiet = false, autoScan = false } = options;
    showError("");

    try {
      await ensureAccessToken();

      const folderName =
        $("folderName").value.trim() ||
        window.MODEL_CONFIG.preferredFolderName ||
        "AI-Model-Vault";

      if (!quiet) setStatus("正在寻找 Drive 文件夹 · " + folderName);

      const result = await window.DriveModelClient.findFolderByName(
        accessToken,
        folderName,
        "root"
      );

      if (!result.folder) {
        setDriveState("没有在 Drive 根目录找到“" + folderName + "”。可手动填写 folder ID。");
        if (!quiet) setStatus("未找到 " + folderName);
        return null;
      }

      saveDriveSelection(result.folder);

      if (result.matches.length > 1) {
        setDriveState(
          "找到 " +
            result.matches.length +
            " 个同名文件夹，默认选择最近修改的 · " +
            result.folder.name
        );
      }

      if (!quiet) setStatus("已找到 " + result.folder.name);
      if (autoScan) await scanDrive();
      return result.folder;
    } catch (error) {
      if (!quiet) {
        showError(error);
        setStatus("寻找 Drive 文件夹失败");
      }
      return null;
    }
  }

  async function openChatBootstrap(button) {
    showError("");
    button.disabled = true;
    try {
      await ensureAccessToken();

      let rootId = $("folderId").value.trim();
      if (!rootId) {
        const root = await findPreferredFolder({ quiet: false });
        if (!root) {
          throw new Error("未找到 AI-Model-Vault，无法定位聊天模型引导器。");
        }
        rootId = root.id;
      }

      const launcherFolder = await window.DriveModelClient.findFolderByName(
        accessToken,
        CHAT_BOOTSTRAP.folderName,
        rootId
      );
      if (!launcherFolder.folder) {
        throw new Error(
          "AI-Model-Vault 中没有 notebook_launchers 文件夹。"
        );
      }

      const notebook = await window.DriveModelClient.findFileByName(
        accessToken,
        CHAT_BOOTSTRAP.fileName,
        launcherFolder.folder.id
      );
      if (!notebook) {
        throw new Error(
          "未找到默认聊天模型引导器：" + CHAT_BOOTSTRAP.fileName
        );
      }

      const url =
        "https://colab.research.google.com/drive/" +
        encodeURIComponent(notebook.id);
      window.open(url, "_blank", "noopener,noreferrer");
      setStatus(
        "已打开 " + CHAT_BOOTSTRAP.modelName +
        " Drive-first 引导器；完成后回到这里重新扫描。"
      );
    } catch (error) {
      showError(error);
      setStatus("无法打开默认聊天模型引导器");
    } finally {
      button.disabled = false;
    }
  }

  async function openFlux2Bootstrap(button) {
    showError("");
    button.disabled = true;
    try {
      await ensureAccessToken();

      let rootId = $("folderId").value.trim();
      if (!rootId) {
        const root = await findPreferredFolder({ quiet: false });
        if (!root) {
          throw new Error("未找到 AI-Model-Vault，无法定位 FLUX.2 依赖引导器。");
        }
        rootId = root.id;
      }

      const launcherFolder = await window.DriveModelClient.findFolderByName(
        accessToken,
        FLUX2_BOOTSTRAP.folderName,
        rootId
      );
      if (!launcherFolder.folder) {
        throw new Error("AI-Model-Vault 中没有 notebook_launchers 文件夹。");
      }

      const notebook = await window.DriveModelClient.findFileByName(
        accessToken,
        FLUX2_BOOTSTRAP.fileName,
        launcherFolder.folder.id
      );
      if (!notebook) {
        throw new Error("未找到 FLUX.2 依赖引导器：" + FLUX2_BOOTSTRAP.fileName);
      }

      const url =
        "https://colab.research.google.com/drive/" +
        encodeURIComponent(notebook.id);
      window.open(url, "_blank", "noopener,noreferrer");
      setStatus(
        "已打开 " + FLUX2_BOOTSTRAP.modelName +
        " 依赖引导器；完成后回到这里重新扫描。"
      );
    } catch (error) {
      showError(error);
      setStatus("无法打开 FLUX.2 依赖引导器");
    } finally {
      button.disabled = false;
    }
  }

  async function openTaskBootstrap(button) {
    showError("");
    button.disabled = true;
    try {
      await ensureAccessToken();

      let rootId = $("folderId").value.trim();
      if (!rootId) {
        const root = await findPreferredFolder({ quiet: false });
        if (!root) {
          throw new Error("未找到 AI-Model-Vault，无法定位专用任务模型引导器。");
        }
        rootId = root.id;
      }

      const launcherFolder = await window.DriveModelClient.findFolderByName(
        accessToken,
        TASK_BOOTSTRAP.folderName,
        rootId
      );
      if (!launcherFolder.folder) {
        throw new Error("AI-Model-Vault 中没有 notebook_launchers 文件夹。");
      }

      const notebook = await window.DriveModelClient.findFileByName(
        accessToken,
        TASK_BOOTSTRAP.fileName,
        launcherFolder.folder.id
      );
      if (!notebook) {
        throw new Error("未找到专用任务模型引导器：" + TASK_BOOTSTRAP.fileName);
      }

      const url =
        "https://colab.research.google.com/drive/" +
        encodeURIComponent(notebook.id);
      window.open(url, "_blank", "noopener,noreferrer");
      setStatus(
        "已打开 " + TASK_BOOTSTRAP.modelName +
        " 引导器；完成后回到这里重新扫描。"
      );
    } catch (error) {
      showError(error);
      setStatus("无法打开专用任务模型引导器");
    } finally {
      button.disabled = false;
    }
  }

  function renderChat() {
    const target = $("chatMessages");
    target.textContent = "";

    if (!chatHistory.length) {
      const empty = document.createElement("div");
      empty.className = "chat-empty";
      empty.textContent = "模型就绪后，这里就是你的本机 AI 对话窗口。";
      target.appendChild(empty);
      return;
    }

    for (const message of chatHistory) {
      const row = document.createElement("div");
      row.className = "chat-message " + message.role;

      const role = document.createElement("span");
      role.className = "chat-role";
      role.textContent = message.role === "user" ? "YOU" : "LOCAL MODEL";

      const body = document.createElement("div");
      body.textContent = message.content;

      row.append(role, body);
      target.appendChild(row);
    }

    target.scrollTop = target.scrollHeight;
  }

  function clearChat() {
    chatHistory = [];
    renderChat();
    showError("");
  }

  async function sendChat() {
    const input = $("chatInput");
    const content = input.value.trim();

    if (!content || chatBusy) return;
    if (!runtimeState || !runtimeState.ready) {
      showError("请先启动并等待本机 GGUF 模型就绪。");
      return;
    }

    chatHistory.push({ role: "user", content });
    input.value = "";
    renderChat();

    chatBusy = true;
    updateChatAvailability();
    setStatus("本机模型正在生成…");
    showError("");

    try {
      const response = await runtimeFetch("/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: chatHistory,
          temperature: Number($("temperature").value || 0.7),
          max_tokens: Number($("maxTokens").value || 512)
        })
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const upstream =
          data.error && data.error.message ? data.error.message : data.error;
        throw new Error(upstream || "生成失败：" + response.status);
      }

      const contentOut =
        data.choices && data.choices[0] && data.choices[0].message
          ? data.choices[0].message.content
          : "";

      if (!contentOut) throw new Error("模型返回了空响应。");

      chatHistory.push({
        role: "assistant",
        content: String(contentOut)
      });
      renderChat();
      setStatus("本机模型回复完成");
    } catch (error) {
      showError(error);
      setStatus("本机模型生成失败");
    } finally {
      chatBusy = false;
      updateChatAvailability();
      input.focus();
    }
  }

  async function inspectModel(model, button) {
    showError("");
    saveRuntimeBase();
    button.disabled = true;
    setStatus("正在检查本机 GGUF 文件…");

    try {
      await syncRuntimeDriveSession();
      const response = await runtimeFetch("/v1/models/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          drive_file_id: model.representativeFile && model.representativeFile.id,
          file_name: model.representativeFile && model.representativeFile.name,
          size: Number(model.representativeFile && model.representativeFile.size || 0),
          md5_checksum: model.representativeFile && model.representativeFile.md5Checksum,
          resource_key: model.representativeFile && model.representativeFile.resourceKey,
          relative_path: model.relativePath
        })
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          data.error ||
            "本机缓存中还没有这个模型。先点“本机启动”，Runtime 会直接从 Google Drive API 下载并缓存。"
        );
      }

      const info = data.gguf || {};
      setStatus(
        "GGUF v" +
          info.version +
          " · " +
          info.tensor_count +
          " tensors · " +
          info.metadata_kv_count +
          " metadata"
      );
    } catch (error) {
      showError(error);
      setStatus("GGUF 检查失败");
    } finally {
      button.disabled = false;
    }
  }

  function backendInfo(model) {
    const map = backendState && backendState.backends ? backendState.backends : {};
    return map[model.backend] || null;
  }

  function videoAdapterFor(model) {
    if (!model) return null;
    const hay = [
      model.id,
      model.name,
      model.repo,
      model.packagePath,
      model.relativePath
    ].filter(Boolean).join(" ").toLowerCase();

    if (
      hay.includes("wan2.2-ti2v-5b") ||
      hay.includes("wan2.2 ti2v 5b")
    ) {
      return {
        id: "wan2.2-ti2v-5b",
        width: 832,
        height: 480,
        frames: 49,
        fps: 24,
        steps: 20,
        cfg: 5,
        minVramMb: 12 * 1024,
        minDiskFreeGb: 10
      };
    }

    if (
      hay.includes("hunyuanvideo-1.5") ||
      hay.includes("hunyuanvideo 1.5")
    ) {
      return {
        id: "hunyuanvideo-1.5",
        width: 1280,
        height: 720,
        frames: 49,
        fps: 24,
        steps: 20,
        cfg: 6,
        lockResolution: true,
        minVramMb: 16 * 1024,
        minDiskFreeGb: 10
      };
    }

    return null;
  }

  function imageAdapterFor(model) {
    if (!model) return null;
    const hay = [
      model.id,
      model.name,
      model.repo,
      model.packagePath,
      model.relativePath
    ].filter(Boolean).join(" ").toLowerCase();

    if (
      hay.includes("pony_diffusion_v6_xl") ||
      hay.includes("pony diffusion v6 xl") ||
      hay.includes("pony-diffusion-v6-xl")
    ) {
      return {
        id: "pony_diffusion_v6_xl",
        artifacts: [
          {
            role: "checkpoint",
            name: "ponyDiffusionV6XL_v6StartWithThisOne.safetensors",
            minBytes: 6000000000
          }
        ],
        minVramMb: 8 * 1024,
        minDiskFreeGb: 10
      };
    }

    if (
      hay.includes("qwen_image_2_1_int8") ||
      hay.includes("qwen-image-2.1") ||
      hay.includes("qwen image 2.1")
    ) {
      return {
        id: "qwen_image_2_1_int8",
        artifacts: [
          {
            role: "unet",
            name: "qwen-image-2.1-Q4_K_M.gguf",
            minBytes: 4500000000
          },
          {
            role: "clip",
            name: "qwen3vl_8b_int8_convrot.safetensors",
            minBytes: 9000000000
          },
          {
            role: "vae",
            name: "qwen_image_2.1_vae_bf16.safetensors",
            minBytes: 650000000
          }
        ],
        minVramMb: 14 * 1024,
        minDiskFreeGb: 18
      };
    }

    if (
      hay.includes("flux2_klein_4b_fp8") ||
      hay.includes("flux.2-klein-4b-fp8") ||
      hay.includes("flux2-klein-4b-fp8") ||
      hay.includes("flux2 klein 4b fp8")
    ) {
      return {
        id: "flux2_klein_4b_fp8",
        artifacts: [
          {
            role: "unet",
            name: "flux-2-klein-4b-fp8.safetensors",
            minBytes: 4000000000,
            expectedBytes: 4070624520
          },
          {
            role: "clip",
            name: "qwen_3_4b.safetensors",
            minBytes: 8000000000,
            expectedBytes: 8044982048
          },
          {
            role: "vae",
            name: "flux2-vae.safetensors",
            minBytes: 330000000,
            expectedBytes: 336213556
          }
        ],
        minVramMb: 10 * 1024,
        minDiskFreeGb: 15
      };
    }

    return null;
  }

  function imageArtifactState(model, adapter) {
    if (!adapter) return { ready: false, detail: "没有图像适配器" };
    const files = Array.isArray(model && model.files) ? model.files : [];
    const artifacts = Array.isArray(adapter.artifacts) ? adapter.artifacts : [];
    if (!artifacts.length) {
      return { ready: false, detail: "图像适配器没有声明固定模型文件" };
    }

    const missing = [];
    const invalid = [];
    for (const artifact of artifacts) {
      const expected = String(artifact.name || "").toLowerCase();
      const match = files.find(file =>
        String(file && file.name || "").toLowerCase() === expected
      );
      if (!match) {
        missing.push(artifact.name);
        continue;
      }
      const size = Number(match.size || 0);
      const exact = Number(artifact.expectedBytes || 0);
      if (
        !Number.isFinite(size) ||
        (exact > 0 && size !== exact) ||
        size < Number(artifact.minBytes || 0)
      ) {
        invalid.push(artifact.name);
      }
    }

    if (missing.length) {
      return {
        ready: false,
        detail: "Drive 缺少固定文件：" + missing.join("、")
      };
    }
    if (invalid.length) {
      return {
        ready: false,
        detail: "Drive 文件大小异常：" + invalid.join("、")
      };
    }
    return {
      ready: true,
      detail: artifacts.length + " 个固定模型文件已确认"
    };
  }

  function taskArtifactState(model) {
    const kind = String(model && model.taskKind || "");
    if (!kind) return { ready: false, detail: "没有专用任务适配器" };

    const expected = kind === "embedding"
      ? { name: "Qwen3-Embedding-0.6B-Q8_0.gguf", bytes: 639150592 }
      : { name: "qwen3-reranker-0.6b-q8_0.gguf", bytes: 639153184 };

    const files = Array.isArray(model && model.files) ? model.files : [];
    const match = files.find(
      file => String(file && file.name || "").toLowerCase() === expected.name.toLowerCase()
    );
    if (!match) {
      return { ready: false, detail: "Drive 缺少固定 GGUF：" + expected.name };
    }
    const size = Number(match.size || 0);
    if (size !== expected.bytes) {
      return {
        ready: false,
        detail: "Drive GGUF 大小不匹配：" + expected.name
      };
    }
    return { ready: true, detail: "固定 task GGUF 已确认" };
  }

  function managedComfyHardware(adapter = null) {
    const hardware = runtimeState && runtimeState.hardware;
    if (!hardware) {
      return { known: false, supported: false, detail: "等待新版 Runtime 检查本机硬件" };
    }

    let supported = !!hardware.managed_comfy_supported;
    const details = [];
    const gpu = hardware.gpu || {};
    const disk = hardware.cache_disk || {};
    const minVramMb = Number(adapter && adapter.minVramMb || 0);
    const minDiskFreeGb = Number(adapter && adapter.minDiskFreeGb || 0);
    const vramMb = Number(gpu.vram_total_mb || 0);
    const diskFreeGb = Number(disk.free_gb || 0);

    if (minVramMb && vramMb < minVramMb) {
      supported = false;
      details.push(
        "显存不足：需要至少 " + (minVramMb / 1024).toFixed(1) +
        " GB，当前约 " + (vramMb / 1024).toFixed(1) + " GB"
      );
    }
    if (minDiskFreeGb && diskFreeGb < minDiskFreeGb) {
      supported = false;
      details.push(
        "缓存磁盘不足：至少保留 " + minDiskFreeGb.toFixed(1) +
        " GB，当前约 " + diskFreeGb.toFixed(1) + " GB"
      );
    }
    if (!supported && !gpu.detected) {
      details.unshift("需要远程 NVIDIA Runtime");
    }
    if (!details.length) {
      details.push(String(hardware.managed_comfy_detail || ""));
    }
    return {
      known: true,
      supported,
      detail: details.filter(Boolean).join("；")
    };
  }

  function stopVideoPolling() {
    if (videoPollTimer) {
      clearTimeout(videoPollTimer);
      videoPollTimer = null;
    }
  }

  function setVideoStatus(text, progress = null) {
    $("videoStatus").textContent = text;
    const bar = $("videoProgressBar");
    if (typeof progress === "number" && Number.isFinite(progress)) {
      bar.style.width = Math.max(0, Math.min(100, progress * 100)) + "%";
    } else {
      bar.style.width = "0%";
    }
  }

  function openVideoWorkspace(model) {
    const adapter = videoAdapterFor(model);
    if (!adapter) {
      showError("这个视频模型还没有网页自动运行适配器。");
      return;
    }

    videoViewEpoch += 1;
    stopVideoPolling();
    clearVideoResult();
    videoReturnFocus = document.activeElement;
    selectedVideoModel = model;
    $("videoWorkspace").hidden = false;
    $("videoModelLabel").textContent =
      model.name + " · " + adapter.id + " · ComfyUI";
    $("videoWidth").value = adapter.width;
    $("videoHeight").value = adapter.height;
    $("videoWidth").disabled = !!adapter.lockResolution;
    $("videoHeight").disabled = !!adapter.lockResolution;
    $("videoFrames").value = adapter.frames;
    $("videoFps").value = adapter.fps;
    $("videoSteps").value = adapter.steps;
    $("videoCfg").value = adapter.cfg;
    $("videoSeed").value = "";
    $("videoResult").hidden = true;
    $("videoResult").removeAttribute("src");
    $("videoEmpty").hidden = false;
    $("videoGenerateBtn").disabled = videoCommandBusy;
    $("videoStopBtn").disabled = true;
    setVideoStatus(
      "已选择 " + model.name + "。填写提示词后点击“生成视频”。"
    );
    $("videoWorkspace").scrollIntoView({ behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    $("videoTitle").focus({ preventScroll: true });
    updateVideoDuration();
    if (runtimeState) pollVideoStatus();
  }

  function clearVideoResult() {
    $("videoResult").pause();
    $("videoResult").removeAttribute("src");
    $("videoResult").hidden = true;
    $("videoDownload").hidden = true;
    $("videoDownload").removeAttribute("href");
    if (videoOutputUrl) URL.revokeObjectURL(videoOutputUrl);
    videoOutputUrl = null;
    videoLastJobId = null;
  }

  function updateVideoDuration() {
    $("videoDuration").textContent = window.ModelVideoControls.duration(Number($("videoFrames").value), Number($("videoFps").value));
  }

  function closeVideoWorkspace() {
    videoViewEpoch += 1;
    stopVideoPolling();
    clearVideoResult();
    selectedVideoModel = null;
    $("videoWorkspace").hidden = true;
    if (videoReturnFocus?.isConnected) videoReturnFocus.focus();
    videoReturnFocus = null;
  }

  function videoPhaseText(state) {
    const phase = String(state && state.phase || "idle");
    const detail = String(state && state.detail || "");
    const labels = {
      idle: "等待任务",
      starting: "正在准备视频任务",
      preparing_comfyui: "首次使用：正在下载 ComfyUI",
      extracting_comfyui: "正在解压 ComfyUI",
      starting_comfyui: "正在启动 ComfyUI",
      downloading_models: "正在准备视频后端模型",
      building_workflow: "正在构建工作流",
      queued: "任务已提交，等待生成",
      generating: "正在生成视频",
      cancelling: "正在停止视频任务",
      complete: "视频生成完成",
      failed: "视频任务失败",
      cancelled: "视频任务已取消"
    };
    return (labels[phase] || phase) + (detail ? " · " + detail : "");
  }

  async function videoFetch(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      return await runtimeFetch(path, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timeout);
    }
  }

  async function pollVideoStatus() {
    if (!selectedVideoModel || videoCommandBusy || videoPollEpoch === videoViewEpoch) return;
    stopVideoPolling();
    const epoch = videoViewEpoch;
    videoPollEpoch = epoch;
    try {
      const response = await videoFetch("/v1/video/status", {
        cache: "no-store"
      });
      const state = await response.json().catch(() => ({}));
      if (epoch !== videoViewEpoch || !selectedVideoModel) return;
      if (!response.ok) {
        throw new Error(state.error || "无法读取视频任务状态。");
      }

      if (typeof state.running !== "boolean" || typeof state.phase !== "string") {
        throw new Error("Runtime 返回了不完整的视频状态，请更新 Runtime 后重试。");
      }
      let progress =
        typeof state.download_progress === "number"
          ? state.download_progress
          : null;
      let text = videoPhaseText(state);
      if (state.running && state.adapter && state.adapter !== videoAdapterFor(selectedVideoModel)?.id) {
        text = "Runtime 正在执行另一模型任务（" + (state.model || state.adapter) + "） · " + text;
      }
      if (state.elapsed_seconds != null && state.started_at) text += " · 已耗时 " + state.elapsed_seconds + " 秒";
      if (state.parameters?.seed != null) text += " · Seed " + state.parameters.seed;

      if (state.current_file) {
        text += " · " + state.current_file;
      }
      if (
        typeof state.download_progress === "number" && state.downloaded_bytes &&
        state.download_total_bytes &&
        state.download_total_bytes > 0
      ) {
        text +=
          " · " +
          formatBytes(state.downloaded_bytes) +
          " / " +
          formatBytes(state.download_total_bytes);
      }

      setVideoStatus(text, progress);
      $("videoStopBtn").disabled = !state.running;
      $("videoGenerateBtn").disabled = !!state.running || videoCommandBusy;

      if (state.phase === "complete" && state.job_id) {
        if (state.adapter !== videoAdapterFor(selectedVideoModel)?.id || state.job_id === videoLastJobId) return;
        const blob = await window.ModelVideoControls.fetchResult(videoFetch, state.job_id);
        if (epoch !== videoViewEpoch || !selectedVideoModel) return;
        clearVideoResult();
        videoOutputUrl = URL.createObjectURL(blob);
        videoLastJobId = state.job_id;
        const video = $("videoResult");
        video.src = videoOutputUrl;
        video.hidden = false;
        const download = $("videoDownload");
        download.href = videoOutputUrl;
        download.download = String(state.output_name || "model-video.mp4").replace(/[^a-zA-Z0-9_.-]/g, "_");
        download.hidden = false;
        $("videoEmpty").hidden = true;
        video.load();
        setStatus("视频生成完成 · " + (state.model || "Video"));
        return;
      }

      if (state.phase === "failed") {
        const message =
          state.error ||
          "视频生成失败。请展开高级诊断查看 Runtime 日志。";
        showError(message);
        setVideoStatus("视频任务失败 · " + message);
        $("videoGenerateBtn").disabled = false;
        $("videoStopBtn").disabled = true;
        return;
      }

      if (state.phase === "cancelled") {
        $("videoGenerateBtn").disabled = false;
        return;
      }

      if (state.running) {
        videoPollTimer = setTimeout(pollVideoStatus, 1500);
      }
    } catch (error) {
      if (epoch !== videoViewEpoch || !selectedVideoModel) return;
      showError(error);
      setVideoStatus("无法确认任务状态，正在重试；不会把连接中断当作完成。");
      $("videoGenerateBtn").disabled = true;
      videoPollTimer = setTimeout(pollVideoStatus, 3000);
    } finally {
      if (videoPollEpoch === epoch) videoPollEpoch = -1;
    }
  }

  async function generateVideo() {
    if (videoCommandBusy || $("videoGenerateBtn").disabled) return;
    if (!selectedVideoModel) {
      showError("请先从模型库选择一个已适配的视频模型。");
      return;
    }

    if (!runtimeState || Number(runtimeState.runtime_version || 0) < 17) {
      showError(
        "请更新到 Model Runtime v0.17，以使用新版视频检查和授权结果下载。"
      );
      return;
    }

    const prompt = $("videoPrompt").value.trim();
    if (!prompt) {
      showError("请先填写视频提示词。");
      $("videoPrompt").focus();
      return;
    }

    const seedText = $("videoSeed").value.trim();
    const adapter = videoAdapterFor(selectedVideoModel);
    if (
      adapter &&
      adapter.lockResolution &&
      (Number($("videoWidth").value) !== adapter.width ||
        Number($("videoHeight").value) !== adapter.height)
    ) {
      $("videoWidth").value = adapter.width;
      $("videoHeight").value = adapter.height;
      showError(
        selectedVideoModel.name +
          " 当前使用固定 " +
          adapter.width +
          "×" +
          adapter.height +
          " 的官方工作流。"
      );
      return;
    }

    const payload = {
      model_id: selectedVideoModel.id,
      name: selectedVideoModel.name,
      package_path:
        selectedVideoModel.packagePath || selectedVideoModel.relativePath,
      prompt,
      negative_prompt: $("videoNegative").value.trim(),
      width: Number($("videoWidth").value),
      height: Number($("videoHeight").value),
      frames: Number($("videoFrames").value),
      fps: Number($("videoFps").value),
      steps: Number($("videoSteps").value),
      cfg: Number($("videoCfg").value)
    };
    if (seedText) payload.seed = Number(seedText);
    try {
      window.ModelVideoControls.validate(payload, adapter);
    } catch (error) {
      showError(error);
      return;
    }

    videoViewEpoch += 1;
    const epoch = videoViewEpoch;
    videoCommandBusy = true;
    stopVideoPolling();
    clearVideoResult();
    showError("");
    $("videoGenerateBtn").disabled = true;
    $("videoStopBtn").disabled = true;
    $("videoEmpty").hidden = false;
    setVideoStatus("正在提交视频任务…");
    setStatus("正在启动 " + selectedVideoModel.name + " 视频工作流…");

    try {
      const response = await videoFetch("/v1/video/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (epoch !== videoViewEpoch || !selectedVideoModel) return;
      if (!response.ok) throw new Error(data.error || "视频任务启动失败：" + response.status);
    } catch (error) {
      if (epoch !== videoViewEpoch || !selectedVideoModel) return;
      showError(error);
      setVideoStatus("提交结果尚未确认；正在检查 Runtime，避免重复启动任务。");
    } finally {
      videoCommandBusy = false;
      if (selectedVideoModel) await pollVideoStatus();
    }
  }

  async function stopVideo() {
    if (videoCommandBusy || !selectedVideoModel || $("videoStopBtn").disabled) return;
    videoCommandBusy = true;
    videoViewEpoch += 1;
    const epoch = videoViewEpoch;
    stopVideoPolling();
    $("videoStopBtn").disabled = true;
    $("videoGenerateBtn").disabled = true;
    setVideoStatus("正在请求停止任务…");
    try {
      const response = await videoFetch("/v1/video/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      const data = await response.json().catch(() => ({}));
      if (epoch !== videoViewEpoch || !selectedVideoModel) return;
      if (!response.ok) throw new Error(data.error || "停止视频任务失败。");
      setVideoStatus(videoPhaseText(data.video || {}));
    } catch (error) {
      if (epoch !== videoViewEpoch || !selectedVideoModel) return;
      showError(error);
      setVideoStatus("停止结果尚未确认，正在重新检查 Runtime。");
    } finally {
      videoCommandBusy = false;
      if (selectedVideoModel) await pollVideoStatus();
    }
  }

  async function loadModelCapabilities() {
    try {
      const response = await runtimeFetch("/v1/models/capabilities", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      modelCapabilities = Object.assign({}, modelCapabilities, Object.fromEntries((data.models || []).map(item => [item.model_id, item])));
    } catch (_) {
      modelCapabilities = window.MODEL_CAPABILITIES || {};
    }
  }

  function capabilityInfo(model) {
    const key = String(model.id || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    return modelCapabilities[key] || null;
  }

  function planText(plan) {
    const status = plan.backend_status || {};
    const requirements = Array.isArray(plan.requirements)
      ? plan.requirements.join(" · ")
      : "";
    return [
      "后端：" + (plan.backend || "未知"),
      "工作区：" + (plan.workspace || "generic"),
      "权重：" + (
        plan.artifact_present === true
          ? "已发现"
          : plan.artifact_present === false
            ? "未就绪"
            : "未知"
      ),
      plan.weight_detail ? "权重说明：" + plan.weight_detail : "",
      "本机检测：" + (status.detected ? "已检测到" : "未检测到"),
      status.detail ? "状态：" + status.detail : "",
      plan.hardware_supported === false ? "硬件：不支持" : "",
      plan.hardware_supported === true ? "硬件：已通过" : "",
      plan.hardware_detail ? "硬件说明：" + plan.hardware_detail : "",
      plan.gguf_preflight && plan.gguf_preflight.disk
        ? "GGUF 磁盘：" + Number(plan.gguf_preflight.disk.free_gb || 0).toFixed(1) + " GB 可用"
        : "",
      plan.gguf_preflight && plan.gguf_preflight.memory
        ? "GGUF 内存：" + (
            plan.gguf_preflight.memory.available_gb == null
              ? "未知"
              : Number(plan.gguf_preflight.memory.available_gb).toFixed(1) + " GB 可用"
          )
        : "",
      plan.gguf_preflight
        ? "GGUF 线程：" + plan.gguf_preflight.threads
        : "",
      plan.gguf_preflight && Array.isArray(plan.gguf_preflight.warnings)
        ? plan.gguf_preflight.warnings.join("；")
        : "",
      plan.package_manifest && !plan.package_manifest.error
        ? (
            "模型包清单：" +
            plan.package_manifest.model_file_count +
            " 权重 + " +
            plan.package_manifest.support_file_count +
            " 支持文件 · " +
            formatBytes(plan.package_manifest.total_bytes)
          )
        : "",
      plan.package_manifest && plan.package_manifest.error
        ? "模型包清单错误：" + plan.package_manifest.error
        : "",
      plan.availability_label ? "模型状态：" + plan.availability_label : "",
      plan.adapter ? "适配器：" + plan.adapter : "",
      plan.availability_reason ? "说明：" + plan.availability_reason : "",
      plan.local_error ? "本机路径：" + plan.local_error : "",
      requirements ? "需要：" + requirements : "",
      plan.note || ""
    ].filter(Boolean).join("\n");
  }

  async function planModel(model, button) {
    showError("");
    saveRuntimeBase();
    button.disabled = true;
    setStatus("正在分析 " + model.name + " 的运行方案…");

    try {
      const response = await runtimeFetch("/v1/models/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backend: model.backend,
          category: model.category,
          package_path: model.packagePath || model.relativePath,
          relative_path: model.relativePath,
          name: model.name,
          model_id: model.id,
          artifact_present: !model.vaultMissing,
          drive_file_id: model.representativeFile && model.representativeFile.id,
          file_name: model.representativeFile && model.representativeFile.name,
          size: Number(model.representativeFile && model.representativeFile.size || 0) || null,
          md5_checksum: model.representativeFile && model.representativeFile.md5Checksum || "",
          resource_key: model.representativeFile && model.representativeFile.resourceKey || "",
          files: (Array.isArray(model.files) ? model.files : []).map(file => ({
            drive_file_id: file.id,
            file_name: file.name,
            size: Number(file.size || 0) || null,
            md5_checksum: file.md5Checksum || "",
            resource_key: file.resourceKey || ""
          })),
          manifest_files: (Array.isArray(model.manifestFiles) ? model.manifestFiles : []).map(file => ({
            drive_file_id: file.id,
            file_name: file.name,
            relative_path: file.relativePath,
            size: Number(file.size || 0) || null,
            md5_checksum: file.md5Checksum || "",
            resource_key: file.resourceKey || ""
          }))
        })
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "运行方案分析失败：" + response.status);

      const plan = data.plan || {};
      const capability = capabilityInfo(model);
      if (capability && !plan.availability_label) {
        plan.availability_label = capability.label;
        plan.availability_reason = capability.reason;
      }
      if (model.vaultMissing) {
        plan.availability_label = "主库未发现文件";
        plan.availability_reason = "该模型在登记表中，但所选 AI-Model-Vault 内没有可扫描的权重文件。";
      }
      $("runtimeLog").textContent = planText(plan);
      setStatus(model.name + " · " + (plan.availability_label || model.backend));
    } catch (error) {
      showError(error);
      setStatus("运行方案分析失败");
    } finally {
      button.disabled = false;
    }
  }

  function renderModels() {
    const list = $("modelList");
    const count = $("modelCount");

    list.textContent = "";
    const visibleModels = window.ModelLibraryControls.filter(models, $("modelSearch").value, $("modelType").value, $("modelFilesOnly").checked);
    const filtered = !!$("modelSearch").value.trim() || $("modelType").value !== "all" || $("modelFilesOnly").checked;
    count.textContent = filtered ? "显示 " + visibleModels.length + " / " + models.length + " 个模型包" : models.length + " 个模型包";
    $("resetModelFilters").disabled = !filtered;

    if (!models.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent =
        "还没有模型索引。连接 Drive 后寻找 AI-Model-Vault，再扫描模型。";
      list.appendChild(empty);
      return;
    }

    if (!visibleModels.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const message = document.createElement("p");
      message.textContent = "没有符合筛选条件的模型。可以缩短关键词、更换类型或重置筛选。";
      const reset = document.createElement("button");
      reset.type = "button";
      reset.textContent = "重置筛选";
      reset.addEventListener("click", resetModelFilters);
      empty.append(message, reset);
      list.appendChild(empty);
      return;
    }

    const hasChatGguf = models.some(
      model =>
        model &&
        model.directLaunch &&
        !model.vaultMissing &&
        model.backend === "llama.cpp"
    );

    if (!hasChatGguf && !filtered) {
      const card = document.createElement("article");
      card.className = "model-card";

      const head = document.createElement("div");
      head.className = "model-card-head";

      const titleWrap = document.createElement("div");
      titleWrap.className = "model-title-wrap";
      const badge = document.createElement("span");
      badge.className = "format-badge";
      badge.textContent = "BOOTSTRAP";
      const title = document.createElement("div");
      title.className = "model-title";
      title.textContent = CHAT_BOOTSTRAP.modelName + " · 默认聊天验证模型";
      titleWrap.append(badge, title);

      const size = document.createElement("div");
      size.className = "model-size";
      size.textContent = CHAT_BOOTSTRAP.approxSize;
      head.append(titleWrap, size);

      const meta = document.createElement("div");
      meta.className = "model-meta";
      meta.textContent =
        "llm · chat · 官方 Qwen GGUF · llama.cpp · Drive-first";

      const path = document.createElement("div");
      path.className = "model-path";
      path.textContent =
        "当前 AI-Model-Vault 尚未发现聊天 GGUF。运行一次 Drive 引导器，" +
        "它会下载并校验官方 Qwen3-0.6B Q8_0；完成后重新扫描即可直接使用。";

      const actions = document.createElement("div");
      actions.className = "model-actions";
      const prepare = document.createElement("button");
      prepare.type = "button";
      prepare.dataset.icon = "play";
      prepare.className = "primary";
      prepare.textContent = "准备默认聊天模型";
      prepare.addEventListener("click", () => openChatBootstrap(prepare));
      actions.appendChild(prepare);

      card.append(head, meta, path, actions);
      list.appendChild(card);
    }

    for (const model of visibleModels) {
      const card = document.createElement("article");
      card.className = "model-card";

      if (
        runtimeState &&
        runtimeState.running &&
        runtimeState.model_relative_path === model.relativePath
      ) {
        card.classList.add("active");
      }

      const head = document.createElement("div");
      head.className = "model-card-head";

      const titleWrap = document.createElement("div");
      titleWrap.className = "model-title-wrap";

      const format = document.createElement("span");
      format.className = "format-badge";
      format.textContent = model.backend || extension(model.representativeFile && model.representativeFile.name);

      const title = document.createElement("div");
      title.className = "model-title";
      title.textContent = model.name;

      titleWrap.append(format, title);

      const size = document.createElement("div");
      size.className = "model-size";
      size.textContent =
        formatBytes(model.totalSize) +
        " · " +
        model.fileCount +
        " 权重" +
        (Number(model.supportFileCount || 0)
          ? " + " + model.supportFileCount + " 支持文件"
          : "");

      head.append(titleWrap, size);

      const meta = document.createElement("div");
      meta.className = "model-meta";
      const info = backendInfo(model);
      const videoAdapter = videoAdapterFor(model);
      const imageAdapter = imageAdapterFor(model);
      const imageArtifact = imageAdapter
        ? imageArtifactState(model, imageAdapter)
        : null;
      const taskArtifact = model.taskKind ? taskArtifactState(model) : null;
      const managedAdapter = videoAdapter || imageAdapter;
      const hardware = managedComfyHardware(managedAdapter);
      const capability = capabilityInfo(model);
      const pieces = [
        model.category || "unknown",
        model.workspace || "generic",
        model.qualityTier || "",
        model.vaultMissing
          ? "主库未发现文件"
          : model.taskKind && taskArtifact && !taskArtifact.ready
            ? "Drive 文件不完整"
          : imageAdapter && imageArtifact && !imageArtifact.ready
            ? "Drive 文件不完整"
            : managedAdapter && hardware.known && !hardware.supported
              ? "硬件不支持"
              : capability
                ? capability.label
                : "",
        videoAdapter ? "网页视频适配已支持" : "",
        imageAdapter ? "网页图像适配已支持" : "",
        model.taskKind ? "llama.cpp 专用任务适配已支持" : "",
        Number(model.supportFileCount || 0)
          ? "索引清单 " + model.packageFileCount + " 文件"
          : "",
        managedAdapter
          ? hardware.known
            ? hardware.supported
              ? "硬件检查已通过"
              : "当前硬件不满足 managed ComfyUI"
            : "等待 Runtime 硬件检查"
          : info
            ? (info.detected ? "后端已检测" : "后端未安装")
            : ""
      ].filter(Boolean);
      meta.textContent = pieces.join(" · ");

      const path = document.createElement("div");
      path.className = "model-path";
      path.textContent = model.vaultMissing
        ? "登记表中存在；所选 AI-Model-Vault 内未发现模型文件。"
        : (model.packagePath || model.relativePath) +
          (model.taskKind && taskArtifact && !taskArtifact.ready
            ? " · " + taskArtifact.detail
            : imageAdapter && imageArtifact && !imageArtifact.ready
              ? " · " + imageArtifact.detail
              : capability && capability.label !== "可直接使用"
                ? " · " + capability.reason
                : "");

      const actions = document.createElement("div");
      actions.className = "model-actions";

      const representative = model.representativeFile;
      if (representative && representative.id && Number(representative.size || 0)) {
        const probe = document.createElement("button");
        probe.type = "button";
        probe.dataset.icon = "chart";
        probe.textContent = "Drive Range";
        probe.addEventListener("click", async () => {
          showError("");
          probe.disabled = true;
          try {
            await ensureAccessToken();
            const result = await window.DriveModelClient.probeRange(
              representative,
              accessToken
            );
            setStatus("Drive Range 成功 · " + result.bytes + " bytes");
          } catch (error) {
            showError(error);
          } finally {
            probe.disabled = false;
          }
        });
        actions.appendChild(probe);
      }

      const plan = document.createElement("button");
      plan.type = "button";
      plan.textContent = "运行方案";
      plan.addEventListener("click", () => planModel(model, plan));
      actions.appendChild(plan);

      if (model.id === "qwen_image_edit_2511" && !model.vaultMissing) {
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "primary";
        edit.textContent = "使用 2511 参考图编辑";
        edit.addEventListener("click", () => {
          if (!window.ModelWorkspaces?.openModel(model)) showError("编辑工作区未加载，请刷新网页。");
        });
        actions.appendChild(edit);
      } else if (
        model.directLaunch &&
        info &&
        info.detected &&
        (!capability || capability.label === "可直接使用")
      ) {
        const inspect = document.createElement("button");
        inspect.type = "button";
        inspect.textContent = "本机检查";
        inspect.addEventListener("click", () => inspectModel(model, inspect));
        actions.appendChild(inspect);

        const launch = document.createElement("button");
        launch.type = "button";
        launch.dataset.icon = "play";
        launch.textContent = "使用模型";
        launch.className = "primary";
        launch.addEventListener("click", () => startModel(model));
        actions.appendChild(launch);
      } else if (
        videoAdapter &&
        hardware.known &&
        hardware.supported &&
        (!capability || capability.label === "可直接使用")
      ) {
        const useVideo = document.createElement("button");
        useVideo.type = "button";
        useVideo.dataset.icon = "play";
        useVideo.textContent = "使用视频模型";
        useVideo.className = "primary";
        useVideo.addEventListener("click", () => openVideoWorkspace(model));
        actions.appendChild(useVideo);
      } else if (
        imageAdapter &&
        imageArtifact &&
        imageArtifact.ready &&
        hardware.known &&
        hardware.supported &&
        (!capability || capability.label === "可直接使用")
      ) {
        const useImage = document.createElement("button");
        useImage.type = "button";
        useImage.dataset.icon = "play";
        useImage.textContent = "使用图像模型";
        useImage.className = "primary";
        useImage.addEventListener("click", () => {
          if (
            window.ModelWorkspaces &&
            typeof window.ModelWorkspaces.openModel === "function"
          ) {
            window.ModelWorkspaces.openModel(model);
          } else {
            showError("图像工作区尚未加载，请刷新网页后重试。");
          }
        });
        actions.appendChild(useImage);
      } else if (
        imageAdapter &&
        imageAdapter.id === "flux2_klein_4b_fp8" &&
        imageArtifact &&
        !imageArtifact.ready &&
        !model.vaultMissing
      ) {
        const repair = document.createElement("button");
        repair.type = "button";
        repair.dataset.icon = "play";
        repair.textContent = "补齐 FLUX.2 依赖";
        repair.className = "primary";
        repair.addEventListener("click", () => openFlux2Bootstrap(repair));
        actions.appendChild(repair);
      } else if (
        model.taskKind &&
        taskArtifact &&
        taskArtifact.ready &&
        info &&
        info.detected &&
        (!capability || capability.label === "可直接使用")
      ) {
        const useTask = document.createElement("button");
        useTask.type = "button";
        useTask.dataset.icon = "play";
        useTask.textContent =
          model.taskKind === "embedding" ? "使用 Embedding" : "使用 Reranker";
        useTask.className = "primary";
        useTask.addEventListener("click", () => {
          if (
            window.ModelWorkspaces &&
            typeof window.ModelWorkspaces.openModel === "function"
          ) {
            window.ModelWorkspaces.openModel(model);
          } else {
            showError("向量工作区尚未加载，请刷新网页后重试。");
          }
        });
        actions.appendChild(useTask);
      } else if (
        model.taskKind &&
        (model.vaultMissing || (taskArtifact && !taskArtifact.ready))
      ) {
        const prepareTask = document.createElement("button");
        prepareTask.type = "button";
        prepareTask.dataset.icon = "play";
        prepareTask.textContent = "准备 Embedding / Reranker";
        prepareTask.className = "primary";
        prepareTask.addEventListener("click", () => openTaskBootstrap(prepareTask));
        actions.appendChild(prepareTask);
      } else if (!model.vaultMissing && (!capability || !["Drive 文件不完整", "依赖模型不完整"].includes(capability.label))) {
        const prepare = document.createElement("button");
        prepare.type = "button";
        prepare.dataset.icon = "play";
        prepare.textContent = "查看运行方案";
        prepare.className = "primary";
        prepare.addEventListener("click", () => planModel(model, prepare));
        actions.appendChild(prepare);
      }

      card.append(head, meta, path, actions);
      list.appendChild(card);
    }
  }

  function resetModelFilters() {
    $("modelSearch").value = "";
    $("modelType").value = "all";
    $("modelFilesOnly").checked = false;
    renderModels();
    $("modelSearch").focus();
  }

  async function scanDrive() {
    if (scanBusy) return;
    scanBusy = true;
    $("modelList").setAttribute("aria-busy", "true");
    showError("");
    const button = $("scanBtn");
    button.disabled = true;

    try {
      await ensureAccessToken();

      let rootId = $("folderId").value.trim();
      if (!rootId) {
        const folder = await findPreferredFolder({ quiet: false });
        if (!folder) {
          throw new Error(
            "没有模型根文件夹 ID。请先寻找 AI-Model-Vault 或手动填写 folder ID。"
          );
        }
        rootId = folder.id;
      }

      localStorage.setItem("model_drive_root_id", rootId);

      const result = await window.DriveModelClient.scanModelTree(
        accessToken,
        rootId,
        progress => {
          setStatus(
            "扫描中 · " +
              progress.scannedFolders +
              " 文件夹 · " +
              progress.modelFiles +
              " 权重 · " +
              Number(progress.supportFiles || 0) +
              " 支持文件"
          );
        }
      );

      const registry = await window.DriveModelClient.loadModelRegistry(
        accessToken,
        rootId
      );

      let linkedFolders = 0;
      const canonicalModels = window.DriveModelIndex.flattenPackages(
        result.tree,
        registry
      );
      for (const linked of LINKED_MODEL_FOLDERS) {
        const canonical = canonicalModels.find(
          item => item.id === linked.modelId && !item.vaultMissing
        );
        if (canonical) continue;

        try {
          const found = await window.DriveModelClient.findFolderByName(
            accessToken,
            linked.folderName,
            linked.parentId === undefined ? "root" : linked.parentId
          );
          if (!found.folder) continue;
          setStatus("正在链接 Drive 模型目录 · " + linked.folderName);
          const linkedResult = await window.DriveModelClient.scanModelTree(
            accessToken,
            found.folder.id
          );
          if (
            window.DriveModelIndex.mountLinkedTree(
              result.tree,
              linkedResult.tree,
              linked.categoryRoot
            )
          ) {
            linkedFolders += 1;
          }
        } catch (error) {
          console.warn("链接模型目录失败：", linked.folderName, error);
        }
      }

      window.DriveModelIndex.saveSnapshot(
        result.rootFolder,
        result.tree,
        registry,
        true
      );

      models = window.DriveModelIndex.flattenPackages(
        result.tree,
        registry
      );
      renderModels();
      setDriveState(
        "已扫描 · " +
          result.rootFolder.name +
          " · " +
          result.scannedFolders +
          " 文件夹"
      );
      setStatus(
        "扫描完成 · " +
          result.scannedFolders +
          " 文件夹 · " +
          result.modelFiles +
          " 权重文件 · " +
          Number(result.supportFiles || 0) +
          " 支持文件 · " +
          models.length +
          " 模型包" +
          (linkedFolders ? " · " + linkedFolders + " 个链接目录" : "")
      );
    } catch (error) {
      showError(error);
      setStatus("扫描失败");
    } finally {
      scanBusy = false;
      $("modelList").setAttribute("aria-busy", "false");
      button.disabled = false;
    }
  }

  async function startModel(model) {
    showError("");
    saveRuntimeBase();
    setStatus("正在请求本机 Runtime…");
    clearChat();

    try {
      await syncRuntimeDriveSession();
      const response = await runtimeFetch("/v1/models/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          drive_file_id: model.representativeFile && model.representativeFile.id,
          file_name: model.representativeFile && model.representativeFile.name,
          display_name: model.name,
          name: model.name,
          relative_path: model.relativePath,
          size: Number(model.representativeFile && model.representativeFile.size || 0),
          md5_checksum: model.representativeFile && model.representativeFile.md5Checksum,
          resource_key: model.representativeFile && model.representativeFile.resourceKey,
          format: model.runnableFormat,
          backend: model.backend,
          category: model.category
        })
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          data.error ||
            "模型启动失败：" +
              response.status +
              "。请确认本机 AI 引擎已连接，并重新连接 Google Drive 后再试。"
        );
      }

      setStatus(
        data.phase === "downloading"
          ? "正在从 Google Drive 下载到本机缓存…"
          : data.ready
            ? "本机模型已就绪"
            : "本机模型正在加载"
      );

      await refreshRuntime();
    } catch (error) {
      showError(error);
      setStatus("本机 Runtime 启动失败");
    }
  }

  async function stopModel() {
    showError("");
    saveRuntimeBase();
    clearRuntimePoll();

    try {
      const response = await runtimeFetch("/v1/models/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "停止失败");

      clearChat();
      setStatus("本机模型已停止");
      await refreshRuntime();
    } catch (error) {
      showError(error);
    }
  }

  window.ModelApp = {
    runtimeBase: () => runtimeBase || window.MODEL_CONFIG.runtimeBase,
    runtimeFetch,
    runtimeHeaders,
    syncRuntimeDriveSession: async () => {
      saveRuntimeBase();
      await ensureAccessToken();
      await syncRuntimeDriveSession();
      return true;
    }
  };

  async function init() {
    window.DriveModelClient.captureOAuthSession();
    await window.DriveModelClient.registerServiceWorker();

    $("folderName").value =
      localStorage.getItem("model_drive_root_name") ||
      window.MODEL_CONFIG.preferredFolderName ||
      "AI-Model-Vault";

    $("folderId").value =
      localStorage.getItem("model_drive_root_id") || "";

    runtimeBase = normalizeRuntimeBase(
      localStorage.getItem("model_runtime_base") ||
      window.MODEL_CONFIG.runtimeBase ||
      "http://127.0.0.1:8765"
    );
    runtimeToken = sessionStorage.getItem("model_runtime_token") || "";

    $("runtimeUrl").value = runtimeBase;
    $("runtimeToken").value = runtimeToken;

    const snapshot = window.DriveModelIndex.loadSnapshot();
    if (snapshot) {
      models = window.DriveModelIndex.flattenPackages(
        snapshot.tree,
        snapshot.registry || null
      );
      renderModels();
      setDriveState(
        "已恢复上次索引 · " +
          (snapshot.rootFolder.name || snapshot.rootFolder.id)
      );
      setStatus("已恢复上次模型索引 · " + models.length + " 个模型包");
    } else {
      renderModels();
    }

    renderChat();

    accessToken = await window.DriveModelClient.getAccessToken();
    $("loginBtn").textContent =
      accessToken ? "Drive 已连接" : "连接 Google Drive";

    if (accessToken) {
      await window.DriveModelClient.setServiceWorkerToken(accessToken);

      if (!$("folderId").value.trim()) {
        const found = await findPreferredFolder({
          quiet: true,
          autoScan: !snapshot
        });
        if (!found && !snapshot) {
          setDriveState(
            "Drive 已连接；未自动找到 AI-Model-Vault，可手动填写 folder ID。"
          );
        }
      } else if (!snapshot) {
        setDriveState("Drive 已连接 · 已保存模型根目录");
      }
    } else {
      setDriveState("尚未连接 Google Drive。连接后模型由 Drive API 读取，不需要桌面版。");
    }

    const runtimeOk = await refreshRuntime();
    if (!runtimeOk) scheduleRuntimeReconnect();
  }

  $("loginBtn").addEventListener("click", () => {
    try {
      window.DriveModelClient.authorizeDrive();
    } catch (error) {
      showError(error);
    }
  });

  $("findVaultBtn").addEventListener("click", async () => {
    await findPreferredFolder({ quiet: false, autoScan: true });
  });

  $("scanBtn").addEventListener("click", scanDrive);
  $("modelSearch").addEventListener("input", renderModels);
  $("modelType").addEventListener("change", renderModels);
  $("modelFilesOnly").addEventListener("change", renderModels);
  $("resetModelFilters").addEventListener("click", resetModelFilters);

  $("runtimeCheckBtn").addEventListener("click", async () => {
    saveRuntimeBase();
    const ok = await refreshRuntime();
    setStatus(
      ok ? "本机 Runtime 已连接" : "无法连接本机 Runtime"
    );
  });

  $("installRuntimeBtn").addEventListener("click", () => {
    window.open(
      "https://github.com/Jvust1/Model#one-time-windows-install",
      "_blank",
      "noopener,noreferrer"
    );
  });

  $("videoGenerateBtn").addEventListener("click", generateVideo);
  $("videoStopBtn").addEventListener("click", stopVideo);
  $("videoCloseBtn").addEventListener("click", closeVideoWorkspace);
  $("videoRefreshBtn").addEventListener("click", pollVideoStatus);
  $("videoFrames").addEventListener("input", updateVideoDuration);
  $("videoFps").addEventListener("input", updateVideoDuration);
  window.addEventListener("pagehide", closeVideoWorkspace);

  $("stopBtn").addEventListener("click", stopModel);
  $("clearChatBtn").addEventListener("click", clearChat);

  $("chatForm").addEventListener("submit", event => {
    event.preventDefault();
    sendChat();
  });

  $("chatInput").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendChat();
    }
  });

  init().catch(showError);
})();
