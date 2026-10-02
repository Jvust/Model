(function () {
  "use strict";

  const CACHE_KEY = "drive-model-index-v3";
  const CACHE_VERSION = 3;

  const MODEL_EXTENSIONS = new Set([
    ".gguf",
    ".safetensors",
    ".onnx",
    ".pt",
    ".pth",
    ".ckpt",
    ".bin",
    ".model",
    ".tflite"
  ]);

  const SUPPORT_EXTENSIONS = new Set([
    ".json",
    ".txt",
    ".yaml",
    ".yml"
  ]);
  const MAX_SUPPORT_FILE_BYTES = 16 * 1024 * 1024;

  const LLAMA_TASK_MODEL_IDS = new Set([
    "qwen3_embedding_0_6b",
    "qwen3_reranker_0_6b"
  ]);

  const CATEGORY_ROOTS = new Set([
    "image_base",
    "image_edit",
    "image_nsfw",
    "image_anime",
    "video_light",
    "video_ultra",
    "llm",
    "reasoning",
    "code",
    "multimodal",
    "ocr",
    "rag",
    "timeseries",
    "novel"
  ]);

  const FILE_FIELDS = [
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
  ];

  function storage() {
    try {
      return window.sessionStorage;
    } catch (_) {
      return null;
    }
  }

  function extensionOf(name) {
    const value = String(name || "");
    const dot = value.lastIndexOf(".");
    return dot >= 0 ? value.slice(dot).toLowerCase() : "";
  }

  function normalizeName(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/gguf/g, "")
      .replace(/[^a-z0-9]+/g, "");
  }

  function isModelFile(file) {
    if (!file || file.mimeType === "application/vnd.google-apps.folder") {
      return false;
    }
    return MODEL_EXTENSIONS.has(extensionOf(file.name));
  }

  function isPackageSupportFile(file) {
    if (!file || file.mimeType === "application/vnd.google-apps.folder") {
      return false;
    }
    const ext = extensionOf(file.name);
    const size = Number(file.size || 0);
    if (!SUPPORT_EXTENSIONS.has(ext)) return false;
    if (!Number.isFinite(size) || size < 0 || size > MAX_SUPPORT_FILE_BYTES) {
      return false;
    }
    return true;
  }

  function copyFile(file) {
    if (!file) return null;
    const out = {};
    for (const field of FILE_FIELDS) {
      if (file[field] !== undefined) out[field] = file[field];
    }
    return out;
  }

  function copyTree(node) {
    if (!node || !node.file) return null;
    return {
      file: copyFile(node.file),
      relativePath: String(node.relativePath || ""),
      scanned: node.scanned === true,
      children: (node.children || []).map(copyTree).filter(Boolean)
    };
  }

  function sanitizeRegistry(registry) {
    if (!registry || !Array.isArray(registry.models)) return null;
    return {
      schema_version: String(registry.schema_version || ""),
      generated_at: String(registry.generated_at || ""),
      purpose: String(registry.purpose || ""),
      device_profiles: registry.device_profiles || {},
      models: registry.models.map(entry => ({
        id: String(entry.id || ""),
        name: String(entry.name || ""),
        repo: String(entry.repo || ""),
        category: String(entry.category || ""),
        modality: Array.isArray(entry.modality) ? entry.modality.map(String) : [],
        capabilities: Array.isArray(entry.capabilities) ? entry.capabilities.map(String) : [],
        artifact_type: String(entry.artifact_type || ""),
        quality_tier: String(entry.quality_tier || ""),
        preferred_artifact: String(entry.preferred_artifact || ""),
        device_fit: entry.device_fit || {},
        recommended_runtime: Array.isArray(entry.recommended_runtime)
          ? entry.recommended_runtime.map(String)
          : [],
        vault_status: String(entry.vault_status || "")
      }))
    };
  }

  function valid(snapshot) {
    return !!(
      snapshot &&
      snapshot.version === CACHE_VERSION &&
      snapshot.rootFolder &&
      snapshot.rootFolder.id &&
      snapshot.tree &&
      snapshot.tree.file &&
      snapshot.tree.file.id
    );
  }

  function loadSnapshot() {
    const target = storage();
    if (!target) return null;
    try {
      const raw = target.getItem(CACHE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return valid(parsed) ? parsed : null;
    } catch (error) {
      console.warn("读取模型索引失败：", error);
      return null;
    }
  }

  function saveSnapshot(rootFolder, tree, registry = null, complete = true) {
    const target = storage();
    if (!target || !rootFolder || !tree) return false;
    const snapshot = {
      version: CACHE_VERSION,
      complete: complete === true,
      savedAt: Date.now(),
      rootFolder: copyFile(rootFolder),
      tree: copyTree(tree),
      registry: sanitizeRegistry(registry)
    };
    try {
      target.setItem(CACHE_KEY, JSON.stringify(snapshot));
      return true;
    } catch (error) {
      console.warn("保存模型索引失败：", error);
      return false;
    }
  }

  function registryCandidates(entry) {
    const values = [entry && entry.name, entry && entry.id];
    if (entry && entry.repo) {
      values.push(entry.repo);
      values.push(String(entry.repo).split("/").pop());
    }
    return values
      .map(normalizeName)
      .filter(value => value.length >= 5);
  }

  function matchRegistry(relativePath, registry) {
    // The existing Drive source is an official sharded Diffusers directory.
    // Recognize it even when the vault registry does not contain a 2511 entry.
    if (String(relativePath || "").split("/").some(part => normalizeName(part) === "qwenimageedit2511")) {
      return { id: "qwen_image_edit_2511", name: "Qwen-Image-Edit-2511",
        repo: "Qwen/Qwen-Image-Edit-2511", category: "image-edit",
        recommended_runtime: ["diffusers"], capabilities: ["reference-image", "scene-edit"] };
    }
    if (!registry || !Array.isArray(registry.models)) return null;
    const normalizedPath = normalizeName(relativePath);
    let best = null;
    let bestScore = 0;

    for (const entry of registry.models) {
      for (const candidate of registryCandidates(entry)) {
        if (normalizedPath.includes(candidate) && candidate.length > bestScore) {
          best = entry;
          bestScore = candidate.length;
        }
      }
    }
    return best;
  }

  function categoryFromPath(relativePath) {
    const first = String(relativePath || "").split("/")[0] || "";
    if (first.startsWith("image_")) {
      if (first === "image_edit") return "image-edit";
      if (first === "image_anime") return "image-anime";
      if (first === "image_nsfw") return "image-nsfw";
      return "image";
    }
    if (first.startsWith("video_")) return "video";
    return first || "unknown";
  }

  function packageRoot(relativePath, registryEntry) {
    const parts = String(relativePath || "").split("/").filter(Boolean);
    if (parts.length <= 1) return parts[0] || "";

    const parent = parts.slice(0, -1);
    if (CATEGORY_ROOTS.has(parts[0]) && parts.length >= 3) {
      return parts.slice(0, 2).join("/");
    }

    if (registryEntry) {
      const candidates = registryCandidates(registryEntry);
      for (let i = 0; i < parent.length; i += 1) {
        const segment = normalizeName(parent[i]);
        if (candidates.some(candidate => segment.includes(candidate) || candidate.includes(segment))) {
          return parent.slice(0, i + 1).join("/");
        }
      }
    }

    return parent.join("/");
  }

  function chooseBackend(entry, files) {
    if (entry && entry.id === "qwen_image_edit_2511") return "Diffusers";
    const extensions = new Set(files.map(file => extensionOf(file.name)));
    const runtimes = new Set(
      (entry && entry.recommended_runtime ? entry.recommended_runtime : [])
        .map(value => String(value).toLowerCase())
    );
    const inferredCategory = files.length
      ? categoryFromPath(files[0].relativePath)
      : "";
    const category =
      String(entry && entry.category || "") ||
      inferredCategory;

    // Dedicated task GGUFs intentionally override older registry runtime labels.
    if (
      extensions.has(".gguf") &&
      entry &&
      LLAMA_TASK_MODEL_IDS.has(String(entry.id || ""))
    ) {
      return "llama.cpp";
    }

    // Registry routing wins over a file extension for mixed model packages.
    // Unregistered GGUFs are only direct chat candidates when their path lives
    // under an explicit chat-like category root.
    if (runtimes.has("llama.cpp")) return "llama.cpp";
    if (runtimes.has("comfyui")) return "ComfyUI";
    if (runtimes.has("diffusers")) return "Diffusers";
    if (
      extensions.has(".gguf") &&
      ["llm", "reasoning", "code", "novel"].includes(category)
    ) {
      return "llama.cpp";
    }
    if (runtimes.has("transformers")) return "Transformers";
    if (runtimes.has("pytorch")) return "PyTorch";
    if (extensions.has(".onnx")) return "ONNX Runtime";
    if (extensions.has(".tflite")) return "TFLite";

    if (["image", "image-edit", "image-anime", "image-nsfw", "video"].includes(category)) {
      return "ComfyUI";
    }
    if (["ocr", "rag", "multimodal"].includes(category)) return "Transformers";
    if (category === "timeseries") return "PyTorch";
    return "Manual";
  }

  function workspaceForCategory(category) {
    return {
      llm: "chat",
      reasoning: "chat",
      code: "chat",
      novel: "chat",
      multimodal: "vision",
      ocr: "vision",
      image: "image-generation",
      "image-anime": "image-generation",
      "image-nsfw": "image-generation",
      "image-edit": "image-edit",
      video: "video-generation",
      rag: "embedding",
      timeseries: "timeseries"
    }[category] || "generic";
  }

  function taskKindFor(entry) {
    const id = String(entry && entry.id || "");
    if (id === "qwen3_embedding_0_6b") return "embedding";
    if (id === "qwen3_reranker_0_6b") return "reranker";
    return null;
  }

  function flattenPackages(rootNode, registry = null) {
    const rawFiles = [];
    const rawSupportFiles = [];

    function walk(node) {
      if (!node) return;
      const relativePath = String(node.relativePath || "");
      if (relativePath.split("/").some(part => [".cache", ".hf-cache", "offload", "inputs", "outputs"].includes(part)) &&
          relativePath.toLowerCase().includes("qwen-image-edit-2511")) return;
      if (
        node.file &&
        !relativePath.startsWith(".hf-cache/")
      ) {
        if (isModelFile(node.file)) {
          rawFiles.push({
            ...copyFile(node.file),
            relativePath
          });
        } else if (isPackageSupportFile(node.file)) {
          rawSupportFiles.push({
            ...copyFile(node.file),
            relativePath
          });
        }
      }
      for (const child of node.children || []) walk(child);
    }

    walk(rootNode);

    const groups = new Map();

    for (const file of rawFiles) {
      const entry = matchRegistry(file.relativePath, registry);
      const root = packageRoot(file.relativePath, entry);
      const key = root || file.relativePath;

      if (!groups.has(key)) {
        groups.set(key, {
          packagePath: root,
          files: [],
          registry: entry
        });
      }

      const group = groups.get(key);
      group.files.push(file);
      if (!group.registry && entry) group.registry = entry;
    }

    for (const file of rawSupportFiles) {
      const entry = matchRegistry(file.relativePath, registry);
      const root = packageRoot(file.relativePath, entry);
      const key = root || file.relativePath;
      const group = groups.get(key);
      if (!group) continue;
      group.supportFiles = group.supportFiles || [];
      group.supportFiles.push(file);
    }

    const packages = Array.from(groups.values()).map(group => {
      const entry = group.registry || null;
      const files = group.files.sort((a, b) =>
        String(a.relativePath).localeCompare(String(b.relativePath))
      );
      const supportFiles = (group.supportFiles || []).sort((a, b) =>
        String(a.relativePath).localeCompare(String(b.relativePath))
      );
      const manifestFiles = [...files, ...supportFiles].sort((a, b) =>
        String(a.relativePath).localeCompare(String(b.relativePath))
      );
      const gguf = files.find(file => extensionOf(file.name) === ".gguf");
      const representative =
        gguf ||
        [...files].sort((a, b) => Number(b.size || 0) - Number(a.size || 0))[0];

      const category =
        String(entry && entry.category || "") ||
        categoryFromPath(representative.relativePath);

      const backend = chooseBackend(entry, files);
      const totalSize = files.reduce((sum, file) => sum + Number(file.size || 0), 0);

      return {
        id: entry && entry.id ? entry.id : group.packagePath || representative.id,
        name:
          (entry && entry.name) ||
          (group.packagePath ? group.packagePath.split("/").pop() : representative.name),
        repo: entry && entry.repo ? entry.repo : "",
        category,
        modality: entry && entry.modality ? entry.modality : [],
        capabilities: entry && entry.capabilities ? entry.capabilities : [],
        artifactType: entry && entry.artifact_type ? entry.artifact_type : "",
        qualityTier: entry && entry.quality_tier ? entry.quality_tier : "",
        preferredArtifact: entry && entry.preferred_artifact ? entry.preferred_artifact : "",
        deviceFit: entry && entry.device_fit ? entry.device_fit : {},
        recommendedRuntime:
          entry && entry.recommended_runtime ? entry.recommended_runtime : [],
        vaultStatus: entry && entry.vault_status ? entry.vault_status : "",
        backend,
        workspace: workspaceForCategory(category),
        taskKind: taskKindFor(entry),
        packagePath: group.packagePath,
        files,
        supportFiles,
        manifestFiles,
        fileCount: files.length,
        supportFileCount: supportFiles.length,
        packageFileCount: manifestFiles.length,
        vaultMissing: false,
        totalSize,
        representativeFile: representative,
        directLaunch:
          backend === "llama.cpp" &&
          extensionOf(representative.name) === ".gguf" &&
          ["llm", "reasoning", "code", "novel"].includes(category),
        relativePath: representative.relativePath,
        runnableFormat:
          backend === "llama.cpp" && extensionOf(representative.name) === ".gguf"
            ? "gguf"
            : null
      };
    });

    const represented = new Set(packages.map(item => item.id));
    if (registry && Array.isArray(registry.models)) {
      for (const entry of registry.models) {
        if (!entry.id || represented.has(entry.id)) continue;
        const category = String(entry.category || "unknown");
        packages.push({
          id: entry.id,
          name: entry.name || entry.id,
          repo: entry.repo || "",
          category,
          modality: entry.modality || [],
          capabilities: entry.capabilities || [],
          artifactType: entry.artifact_type || "",
          qualityTier: entry.quality_tier || "",
          preferredArtifact: entry.preferred_artifact || "",
          deviceFit: entry.device_fit || {},
          recommendedRuntime: entry.recommended_runtime || [],
          vaultStatus: entry.vault_status || "",
          backend: chooseBackend(entry, []),
          workspace: workspaceForCategory(category),
          taskKind: taskKindFor(entry),
          packagePath: "",
          files: [],
          supportFiles: [],
          manifestFiles: [],
          fileCount: 0,
          supportFileCount: 0,
          packageFileCount: 0,
          vaultMissing: true,
          totalSize: 0,
          representativeFile: null,
          directLaunch: false,
          relativePath: "",
          runnableFormat: null
        });
      }
    }

    return packages.sort((a, b) => {
      const categoryCompare = String(a.category).localeCompare(String(b.category));
      return categoryCompare || String(a.name).localeCompare(String(b.name));
    });
  }

  function mountLinkedTree(rootNode, linkedTree, categoryRoot) {
    if (!rootNode || !linkedTree || !linkedTree.file) return false;
    const category = String(categoryRoot || "").trim();
    if (!category) return false;

    const linkedRootName = String(linkedTree.file.name || "").trim();
    if (!linkedRootName) return false;
    const mountPath = category + "/" + linkedRootName;

    function cloneWithPrefix(node, isRoot = false) {
      const suffix = isRoot
        ? ""
        : String(node.relativePath || "").replace(/^\/+/, "");
      return {
        file: copyFile(node.file),
        relativePath: suffix ? mountPath + "/" + suffix : mountPath,
        scanned: node.scanned === true,
        children: (node.children || [])
          .map(child => cloneWithPrefix(child, false))
          .filter(Boolean)
      };
    }

    let categoryNode = (rootNode.children || []).find(
      child =>
        child &&
        child.file &&
        child.file.mimeType === "application/vnd.google-apps.folder" &&
        child.relativePath === category
    );

    if (!categoryNode) {
      categoryNode = {
        file: {
          id: "linked-category-" + category,
          name: category,
          mimeType: "application/vnd.google-apps.folder"
        },
        relativePath: category,
        scanned: true,
        children: []
      };
      rootNode.children = rootNode.children || [];
      rootNode.children.push(categoryNode);
    }

    const existing = (categoryNode.children || []).find(
      child => child && child.relativePath === mountPath
    );
    if (existing) return false;

    categoryNode.children = categoryNode.children || [];
    categoryNode.children.push(cloneWithPrefix(linkedTree, true));
    return true;
  }

  function countFolders(rootNode) {
    let count = 0;
    function walk(node) {
      if (!node) return;
      if (node.file && node.file.mimeType === "application/vnd.google-apps.folder") {
        count += 1;
      }
      for (const child of node.children || []) walk(child);
    }
    walk(rootNode);
    return Math.max(0, count - 1);
  }

  window.DriveModelIndex = {
    MODEL_EXTENSIONS,
    SUPPORT_EXTENSIONS,
    countFolders,
    extensionOf,
    flattenModels: flattenPackages,
    flattenPackages,
    isModelFile,
    isPackageSupportFile,
    loadSnapshot,
    matchRegistry,
    mountLinkedTree,
    saveSnapshot,
    workspaceForCategory
  };
})();
