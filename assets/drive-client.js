(function () {
  "use strict";

  const CONFIG = window.MODEL_CONFIG || {};
  const SESSION_KEY = "drive_oauth_session";
  const FOLDER_MIME = "application/vnd.google-apps.folder";

  function bridge() {
    return CONFIG.oauthBridge || "";
  }

  function captureOAuthSession() {
    const params = new URLSearchParams(window.location.hash.substring(1));
    const session = params.get("oauth_session");
    if (!session) return false;

    localStorage.setItem(SESSION_KEY, session);
    history.replaceState(null, "", window.location.pathname + window.location.search);
    return true;
  }

  function authorizeDrive() {
    if (!bridge()) throw new Error("未配置 OAuth Bridge。");
    const returnTo = window.location.origin + window.location.pathname;
    const params = new URLSearchParams({ return_to: returnTo });
    window.location.href = bridge() + "/auth?" + params.toString();
  }

  async function getAccessToken() {
    const session = localStorage.getItem(SESSION_KEY);
    if (!session) return null;

    try {
      const response = await fetch(bridge() + "/token", {
        method: "GET",
        headers: { Authorization: "Bearer " + session },
        cache: "no-store"
      });

      if (!response.ok) {
        if (response.status === 401) localStorage.removeItem(SESSION_KEY);
        return null;
      }

      const data = await response.json();
      return data.access_token || null;
    } catch (error) {
      console.error("获取 Drive Access Token 失败：", error);
      return null;
    }
  }

  function clearSession() {
    localStorage.removeItem(SESSION_KEY);
  }

  function escapeQueryLiteral(value) {
    return String(value).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  }

  function authHeaders(accessToken, resourceKey, fileId) {
    const headers = { Authorization: "Bearer " + accessToken };
    if (resourceKey && fileId) {
      headers["X-Goog-Drive-Resource-Keys"] = fileId + "/" + resourceKey;
    }
    return headers;
  }

  async function fetchMetadata(accessToken, fileId, resourceKey = null) {
    const fields = [
      "id",
      "name",
      "mimeType",
      "size",
      "modifiedTime",
      "md5Checksum",
      "resourceKey",
      "parents",
      "driveId",
      "capabilities"
    ].join(",");

    const params = new URLSearchParams({
      supportsAllDrives: "true",
      fields
    });

    const response = await fetch(
      "https://www.googleapis.com/drive/v3/files/" +
        encodeURIComponent(fileId || "root") +
        "?" +
        params.toString(),
      {
        headers: authHeaders(accessToken, resourceKey, fileId),
        cache: "no-store"
      }
    );

    if (!response.ok) {
      throw new Error("读取 Drive 文件元数据失败：" + response.status);
    }
    return response.json();
  }

  async function listChildren(accessToken, folderId, resourceKey = null, pageToken = "") {
    const params = new URLSearchParams({
      q: "'" + escapeQueryLiteral(folderId) + "' in parents and trashed = false",
      fields:
        "nextPageToken,files(id,name,mimeType,size,modifiedTime,md5Checksum,resourceKey,parents,driveId,capabilities)",
      pageSize: "1000",
      orderBy: "folder,name",
      supportsAllDrives: "true",
      includeItemsFromAllDrives: "true"
    });
    if (pageToken) params.set("pageToken", pageToken);

    const response = await fetch(
      "https://www.googleapis.com/drive/v3/files?" + params.toString(),
      {
        headers: authHeaders(accessToken, resourceKey, folderId),
        cache: "no-store"
      }
    );

    if (!response.ok) {
      throw new Error("读取 Drive 文件夹失败：" + response.status);
    }
    return response.json();
  }

  async function findFileByName(accessToken, name, parentId = "root") {
    if (!accessToken) throw new Error("Google Drive 尚未授权。");

    const fileName = String(name || "").trim();
    const parent = String(parentId || "root").trim() || "root";
    if (!fileName) throw new Error("文件名称不能为空。");

    const query = [
      "name = '" + escapeQueryLiteral(fileName) + "'",
      "'" + escapeQueryLiteral(parent) + "' in parents",
      "trashed = false"
    ].join(" and ");

    const params = new URLSearchParams({
      q: query,
      fields: "files(id,name,mimeType,size,modifiedTime,resourceKey,parents,driveId)",
      pageSize: "20",
      orderBy: "modifiedTime desc",
      supportsAllDrives: "true",
      includeItemsFromAllDrives: "true"
    });

    const response = await fetch(
      "https://www.googleapis.com/drive/v3/files?" + params.toString(),
      {
        headers: { Authorization: "Bearer " + accessToken },
        cache: "no-store"
      }
    );

    if (!response.ok) {
      throw new Error("查找 Drive 文件失败：" + response.status);
    }

    const data = await response.json();
    const files = Array.isArray(data.files) ? data.files : [];
    return files[0] || null;
  }

  async function fetchJsonFile(accessToken, file) {
    if (!file || !file.id) throw new Error("缺少 Drive JSON 文件 ID。");

    const response = await fetch(
      "https://www.googleapis.com/drive/v3/files/" +
        encodeURIComponent(file.id) +
        "?alt=media&supportsAllDrives=true",
      {
        headers: authHeaders(accessToken, file.resourceKey || null, file.id),
        cache: "no-store"
      }
    );

    if (!response.ok) {
      throw new Error("读取 Drive JSON 文件失败：" + response.status);
    }

    const data = await response.json();
    if (!data || typeof data !== "object") {
      throw new Error("Drive JSON 文件内容无效。");
    }
    return data;
  }

  async function loadModelRegistry(accessToken, rootId) {
    const file = await findFileByName(
      accessToken,
      "model_metadata.json",
      rootId || "root"
    );
    if (!file) return null;

    try {
      return await fetchJsonFile(accessToken, file);
    } catch (error) {
      console.warn("读取 model_metadata.json 失败：", error);
      return null;
    }
  }

  async function findFolderByName(accessToken, name, parentId = "root") {
    if (!accessToken) throw new Error("Google Drive 尚未授权。");

    const folderName = String(name || "").trim();
    // Explicit null finds nested linked folders; default lookup stays root-only.
    const parent = parentId === null ? null : String(parentId || "root").trim() || "root";
    if (!folderName) throw new Error("文件夹名称不能为空。");

    const query = [
      "mimeType = '" + FOLDER_MIME + "'",
      "name = '" + escapeQueryLiteral(folderName) + "'",
      parent === null ? null : "'" + escapeQueryLiteral(parent) + "' in parents",
      "trashed = false"
    ].filter(Boolean).join(" and ");

    const params = new URLSearchParams({
      q: query,
      fields: "files(id,name,mimeType,modifiedTime,resourceKey,parents,driveId)",
      pageSize: "100",
      orderBy: "modifiedTime desc",
      supportsAllDrives: "true",
      includeItemsFromAllDrives: "true"
    });

    const response = await fetch(
      "https://www.googleapis.com/drive/v3/files?" + params.toString(),
      {
        headers: { Authorization: "Bearer " + accessToken },
        cache: "no-store"
      }
    );

    if (!response.ok) {
      throw new Error("查找 Drive 文件夹失败：" + response.status);
    }

    const data = await response.json();
    const folders = Array.isArray(data.files) ? data.files : [];
    if (parent === null && folders.length > 1) {
      throw new Error("Drive 中有多个同名文件夹：" + folderName + "。请在主模型库中保留一个明确的模型包，避免选错文件。");
    }
    return {
      folder: folders[0] || null,
      matches: folders
    };
  }

  async function scanModelTree(accessToken, requestedRootId, onProgress) {
    if (!accessToken) throw new Error("Google Drive 尚未授权。");

    const rootId = String(requestedRootId || "root").trim() || "root";
    const rootFile = await fetchMetadata(accessToken, rootId);
    if (rootFile.mimeType !== FOLDER_MIME) {
      throw new Error("指定的 Drive ID 不是文件夹。");
    }

    const rootNode = {
      file: rootFile,
      relativePath: "",
      scanned: false,
      children: []
    };

    const queue = [rootNode];
    const visitedFolders = new Set([rootFile.id]);
    let scannedFolders = 0;
    let modelFiles = 0;
    let supportFiles = 0;
    const maxFolders = Number(CONFIG.maxFolders || 2000);
    const maxModelFiles = Number(CONFIG.maxModelFiles || 20000);
    const maxSupportFiles = Number(CONFIG.maxSupportFiles || 40000);

    while (queue.length) {
      const node = queue.shift();
      scannedFolders += 1;

      if (scannedFolders > maxFolders) {
        throw new Error("文件夹数量超过安全上限 " + maxFolders + "。");
      }

      let pageToken = "";
      const seenPages = new Set();
      const seenFiles = new Set();
      do {
        if (seenPages.has(pageToken)) throw new Error("Drive 返回重复分页标记，请稍后重新扫描。");
        seenPages.add(pageToken);
        const page = await listChildren(
          accessToken,
          node.file.id,
          node.file.resourceKey || null,
          pageToken
        );

        for (const file of page.files || []) {
          if (!file.id || seenFiles.has(file.id)) continue;
          seenFiles.add(file.id);
          const childPath = node.relativePath
            ? node.relativePath + "/" + file.name
            : file.name;

          if (file.mimeType === FOLDER_MIME) {
            // Prune before issuing requests, not only after building the index.
            const in2511 = (rootFile.name + "/" + childPath).toLowerCase().includes("qwen-image-edit-2511");
            if (in2511 && [".cache", ".hf-cache", "offload", "inputs", "outputs"].includes(String(file.name).toLowerCase())) continue;
            if (visitedFolders.has(file.id)) continue;
            visitedFolders.add(file.id);
            const child = {
              file,
              relativePath: childPath,
              scanned: false,
              children: []
            };
            node.children.push(child);
            queue.push(child);
          } else if (window.DriveModelIndex.isModelFile(file)) {
            modelFiles += 1;
            if (modelFiles > maxModelFiles) {
              throw new Error("模型文件数量超过安全上限 " + maxModelFiles + "。");
            }
            node.children.push({
              file,
              relativePath: childPath,
              scanned: true,
              children: []
            });
          } else if (window.DriveModelIndex.isPackageSupportFile(file)) {
            supportFiles += 1;
            if (supportFiles > maxSupportFiles) {
              throw new Error("模型支持文件数量超过安全上限 " + maxSupportFiles + "。");
            }
            node.children.push({
              file,
              relativePath: childPath,
              scanned: true,
              children: []
            });
          }
        }

        pageToken = page.nextPageToken || "";
      } while (pageToken);

      node.scanned = true;
      if (typeof onProgress === "function") {
        onProgress({
          scannedFolders,
          modelFiles,
          supportFiles,
          currentFolder: node.file.name
        });
      }
    }

    return {
      rootFolder: rootFile,
      tree: rootNode,
      scannedFolders,
      modelFiles,
      supportFiles
    };
  }

  async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return false;
    try {
      await navigator.serviceWorker.register("./sw.js", { scope: "./" });
      await navigator.serviceWorker.ready;
      return true;
    } catch (error) {
      console.warn("Service Worker 初始化失败：", error);
      return false;
    }
  }

  async function setServiceWorkerToken(accessToken) {
    if (!("serviceWorker" in navigator) || !accessToken) return false;
    const registration = await navigator.serviceWorker.ready;
    const worker =
      navigator.serviceWorker.controller ||
      registration.active ||
      registration.waiting;

    if (!worker) return false;
    worker.postMessage({ type: "SET_TOKEN", token: accessToken });
    return true;
  }

  async function probeRange(file, accessToken) {
    if (!file || !file.id || !Number(file.size)) {
      throw new Error("该文件缺少 Drive ID 或大小。");
    }

    await setServiceWorkerToken(accessToken);

    const bytes = Number(CONFIG.rangeProbeBytes || 4096);
    const params = new URLSearchParams({ size: String(file.size) });
    if (file.resourceKey) params.set("resourceKey", file.resourceKey);

    const response = await fetch(
      "./drive-model/" + encodeURIComponent(file.id) + "?" + params.toString(),
      {
        headers: { Range: "bytes=0-" + (bytes - 1) },
        cache: "no-store"
      }
    );

    if (!response.ok && response.status !== 206) {
      throw new Error("Range 测试失败：" + response.status);
    }

    const buffer = await response.arrayBuffer();
    return {
      status: response.status,
      bytes: buffer.byteLength,
      contentRange: response.headers.get("Content-Range") || ""
    };
  }

  window.DriveModelClient = {
    authorizeDrive,
    captureOAuthSession,
    clearSession,
    fetchMetadata,
    fetchJsonFile,
    findFileByName,
    findFolderByName,
    getAccessToken,
    loadModelRegistry,
    probeRange,
    registerServiceWorker,
    scanModelTree,
    setServiceWorkerToken
  };
})();
