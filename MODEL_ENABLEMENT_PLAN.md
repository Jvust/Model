# 模型启用计划

## 目标

用户在网页中选择模型后，系统应根据模型家族自动选择正确的运行后端，完成必要的依赖准备，并进入可产出状态。网页只把已经具备真实权重、运行适配器和本机硬件条件的模型标记为可启动。

## 当前状态

### 已接通

- Wan2.2 TI2V 5B：ComfyUI 视频工作流。
- HunyuanVideo 1.5：ComfyUI 视频工作流。
- GGUF 聊天链路：Drive API 按需下载、`D:\Model` 持久缓存、llama.cpp 启动。
- 模型库扫描：读取 `AI-Model-Vault`，区分登记项、实际权重和运行适配器。
- Runtime：本机服务、缓存管理、能力接口和模型运行方案接口。

### 当前阻塞

- 图像模型有部分权重，但缺少对应的 ComfyUI 或 Diffusers 工作流。
- Drive 主库目前没有部分聊天、OCR、Embedding、Reranker 和时间序列模型的实际权重。
- 视频工作流当前依赖 Windows NVIDIA CUDA；本机 AMD Vega 8 无法运行这条链路。
- 任意 `.safetensors`、`.pth`、`.ckpt` 文件不能仅凭扩展名直接启动，还需要架构、依赖、预处理器和输出节点配置。

## 模型启用规则

一个模型只有同时满足下面三个条件，网页才提供直接启动入口：

1. `AI-Model-Vault` 中存在可用权重文件。
2. Runtime 中存在该模型家族的专用适配器或固定工作流。
3. 当前机器具备所需的后端、显存、内存和磁盘条件。

不满足条件时，网页显示具体原因：

- **主库未发现文件**：登记表有模型，但 Drive 没有扫描到权重。
- **Drive 文件不完整**：目录存在，但主权重或依赖不完整。
- **需要适配器**：权重存在，但输入输出接口尚未接通。
- **需要工作流**：权重存在，但 ComfyUI/Diffusers 工作流尚未接通。
- **硬件不支持**：后端存在，但当前设备不符合运行要求。

## 实施顺序

### 第一阶段：修正启用入口

- 模型卡片只显示与实际状态匹配的按钮。
- 已有直接启动器的模型显示“使用模型”或“使用视频模型”。
- 其他模型显示“查看运行方案”，不再显示会误导用户的“准备 ComfyUI”。
- 运行方案同时显示权重、适配器、后端和硬件检查结果。
- 缺少权重的模型不允许进入下载或启动流程。

### 第二阶段：接通 Drive 中已有的图像模型

优先处理当前 Drive 中权重完整的模型：

1. Pony Diffusion V6 XL。
2. FLUX2 Klein 4B FP8。
3. 其他经过完整性检查、依赖齐全的图像模型。

每个模型需要固定：

- ComfyUI 节点或 Diffusers pipeline。
- 文本编码器、VAE 和其他依赖。
- 输入尺寸、采样步数、CFG 和种子参数。
- 输出文件回传接口。
- 模型专用的错误信息和缓存目录。

### 第三阶段：接通聊天模型

- 在 Drive 中补充至少一个实际存在的小型 GGUF 模型，作为网页默认聊天模型。
- 通过 llama.cpp 复用现有下载、断点续传和 `D:\Model` 缓存逻辑。
- 为 Qwen、DeepSeek、Coder 和写作模型分别保存模型参数和上下文设置。
- 启动前检查 GGUF 文件、磁盘空间、线程数和可用内存。

### 第四阶段：接通专用任务模型

按任务类型分别实现适配器：

- GOT-OCR：图片输入、OCR 预处理和文本输出。
- Qwen Embedding：文本或文档分块、向量化和批处理输出。
- Qwen Reranker：查询与候选文档输入、排序分数输出。
- Chronos / TimesFM：时间序列输入、预测窗口和结果导出。

这些模型不使用通用 ComfyUI 按钮，而是在网页中提供对应的任务工作区。

### 第五阶段：处理视频硬件限制

- 保留现有 NVIDIA Windows ComfyUI 视频适配器。
- 在运行前明确检查 `nvidia-smi`、CUDA、显存和磁盘。
- 对 AMD 或无 NVIDIA 的机器显示“需要远程 NVIDIA Runtime”。
- 后续接入远程 GPU Runtime，网页保持同一套提示词、进度和结果回传接口。

## 缓存策略

- 默认缓存根目录：`D:\Model`。
- 模型按需读取和下载，不在启动网页时预下载全部模型。
- 已完成文件永久保留，除非用户在缓存管理界面主动删除。
- 视频后端缓存：`D:\Model\video`。
- 不同后端和模型使用独立子目录，避免文件名冲突。
- 下载失败保留断点文件，重新运行时继续下载。

## 网页工作流

1. 打开网页并连接 Google Drive。
2. 寻找或选择 `AI-Model-Vault`。
3. 扫描模型目录和 `model_metadata.json`。
4. 计算每个模型的权重、适配器和硬件状态。
5. 只展示当前可执行的启动入口。
6. 用户点击启用后，Runtime 按需准备依赖和缓存。
7. 后端启动后显示进度、日志和可用的任务输入。
8. 产出完成后直接回传网页，并保留缓存供下次使用。

## 验收标准

### 直接启动模型

- 点击启动后能自动准备后端。
- 不需要用户手动猜测模型目录或依赖文件。
- 失败时显示明确原因和下一步操作。
- 成功后网页可以提交任务并看到结果。

### 状态准确性

- 没有权重的登记项不能显示可启动按钮。
- 没有适配器的权重不能显示可直接使用。
- 硬件不支持时在启动前阻止无效下载。
- 能区分 Drive 文件缺失、适配器缺失和硬件缺失。

### 缓存可靠性

- 首次下载写入 `D:\Model`。
- 中断后可以继续下载。
- 再次启动复用完整缓存。
- 用户主动删除前不清理缓存。

## 当前优先级

1. 让已有图像权重至少有一个可用的网页工作流。
2. 在 Drive 中补充一个实际可用的小型 GGUF，并完成聊天启用。
3. 接入 OCR、Embedding、Reranker 和时间序列任务工作区。
4. 为视频增加远程 NVIDIA Runtime。
5. 扩展更多模型家族和高级参数。

## 推进记录（2026-09-26）

- 第一阶段：已在 PR #17 实现启动入口的后端/硬件门禁；GGUF 需要检测到 llama.cpp，managed ComfyUI 图像/视频需要硬件检查通过，否则仅显示运行方案。
- 第二阶段：Pony Diffusion V6 XL 已加入固定 checkpoint 校验、Drive 断点缓存、managed ComfyUI SDXL 工作流、网页图像任务和结果回传；Windows 打包冒烟测试已覆盖新 API。
- 第二阶段：FLUX.2 Klein 4B FP8 的 Runtime adapter 已完成；现有 4.07GB FP8 主权重已确认是官方 single-file，当前真实阻塞收敛为缺少 `qwen_3_4b.safetensors` 与 `flux2-vae.safetensors` 两个 companion。网页在二者缺失时显示“补齐 FLUX.2 依赖”，不显示直接使用。
- 后续优先级保持不变：补一个真实小型 GGUF → 专用任务模型 → 远程 NVIDIA Runtime → 更多模型家族。

- 第三阶段前置：已建立 llm / reasoning / code / multimodal / ocr / rag / timeseries / novel Vault 分类目录；当前仍未发现聊天用 GGUF，因此不把任何聊天登记项标记为可启动。
- 第三阶段前置（PR #18）：GGUF 运行方案与启动接口增加缓存磁盘、可用内存和 CPU 线程检查；Drive 下载按剩余字节 + 2 GB 安全余量做启动前磁盘预检。
- 第四阶段审计：当前 canonical AI-Model-Vault 及可检索 Drive 中未发现 GOT-OCR、Qwen Embedding、Qwen Reranker、Chronos / TimesFM 的真实权重副本；对应工作区继续保持“需要权重/适配器”。
- 第五阶段（PR #18）：managed ComfyUI 增加 nvidia-smi、CUDA、VRAM 和磁盘预检；无 NVIDIA/CUDA 时明确显示“需要远程 NVIDIA Runtime”，避免先下载大模型再失败。

- 第二阶段继续推进：Qwen-Image 2.1 GGUF 已完成链接式 Drive 扫描、三文件完整性校验、ComfyUI-GGUF 自动准备、固定 API 工作流和网页参数默认值；不复制现有约 14 GB 权重。
- 路由修正：包含 `.gguf` 的图像包按 `model_metadata.json` / 专用适配器路由到 ComfyUI，不再仅凭扩展名误判成 llama.cpp 聊天模型。
- 仓库迁移：权威 GitHub 地址已变更为 `Jvust/Model`；Runtime 同时允许新旧 Pages Origin 以兼容迁移期间访问。

- 第三阶段继续推进：已选定官方 `Qwen/Qwen3-0.6B-GGUF` 的 `Qwen3-0.6B-Q8_0.gguf` 作为默认聊天链路验证模型（约 639 MB）；Drive 引导器已写入 `AI-Model-Vault/notebook_launchers/启动_Qwen3-0.6B_Q8_0_DriveFirst.ipynb`。
- 引导器会把模型写入 canonical `AI-Model-Vault/llm/Qwen3-0.6B-GGUF/`，严格校验官方 SHA256 与 GGUF 固定头；现阶段“引导器已就绪”不等于“权重已经下载完成”。
- 网页在未发现聊天 GGUF 时会显示“准备默认聊天模型”，自动定位 Drive 中的引导器并打开 Colab；完成后重新扫描即可复用现有 Drive API → `D:\Model` → llama.cpp → 网页聊天链路。
- 索引修正：未登记但位于 `llm / reasoning / code / novel` 明确目录下的 `.gguf` 可被识别为 llama.cpp；图像目录中的 GGUF 仍不会被误判为聊天模型。

- 第五阶段继续推进：已实现远程 NVIDIA Runtime 传输层，网页可把 Runtime Base URL 切换到 HTTPS 远程地址，同时保持现有图像/视频/聊天 API 不变。
- 远程模式使用 Tailscale Serve 专用 HTTPS 8443 端口反向代理到 NVIDIA 主机的 `127.0.0.1:8765`；不使用 Funnel，不让 Runtime 监听 LAN/public 地址。
- Runtime v0.13 支持可选 256-bit 令牌；远程脚本自动生成并写入 NVIDIA 主机配置，网页令牌只放 `sessionStorage`。
- 已加入 `Enable-Remote-Nvidia.cmd` / `Disable-Remote-Nvidia.cmd` 和完整文档；当前状态是“实现完成、真实双机验收待做”，不能标记为跨设备已验证。

- 第二阶段继续推进（FLUX.2）：Runtime v0.14 增加固定 distilled 4-step ComfyUI graph，严格要求 FP8 diffusion + Qwen3-4B encoder + FLUX2 VAE 三文件。
- Drive 引导器已写入 `AI-Model-Vault/notebook_launchers/启动_FLUX2-klein-4b-fp8_补齐依赖_DriveFirst.ipynb`；它只补下载缺失的 encoder/VAE，现有 4.07GB 主权重只做官方大小/SHA256 校验，不重复下载。
- 当前固定 ComfyUI v0.37.0 已确认包含 `Flux2Scheduler` 与 `EmptyFlux2LatentImage`；Runtime 仍会在任务前查询 `/object_info`，缺节点时拒绝执行。
- 状态边界：adapter 已接通不等于 Drive 已 ready；在 companion notebook 实际跑完并重新扫描前，FLUX.2 仍保持“Drive 文件不完整/补齐依赖”状态。

- 第二阶段继续推进：已加入 FLUX.2 Klein 4B FP8 固定三文件校验与官方 distilled 4-step ComfyUI graph；pinned ComfyUI v0.37.0 已确认包含 Flux2Scheduler / EmptyFlux2LatentImage 等所需节点。
- FLUX.2 companion 引导器已放入 `AI-Model-Vault/notebook_launchers/启动_FLUX2-klein-4b-fp8_补齐依赖_DriveFirst.ipynb`；只补齐 Qwen3-4B text encoder + FLUX2 VAE，不重复下载现有 4.07GB 主权重，并对三文件做官方 SHA256 校验。
- 网页在 FLUX.2 主权重存在但 companion 缺失时显示“补齐 FLUX.2 依赖”；三文件齐全且硬件通过后才显示“使用图像模型”。

- 第四阶段继续推进：Qwen3 Embedding 0.6B 与 Qwen3 Reranker 0.6B 改用独立 llama.cpp task server，不再依赖 Windows Runtime 内置 PyTorch/Transformers；聊天端口 8080 与 task 端口 8091 相互独立。
- Qwen3 Embedding / Reranker task server 已接入 `/v1/tasks/start|status|stop`、`/v1/tasks/embeddings`、`/v1/tasks/rerank` 和真实网页向量工作区；Embedding 使用 last pooling，Reranker 使用 rank pooling。
- Drive 引导器已放入 `AI-Model-Vault/notebook_launchers/启动_Qwen3_Embedding_Reranker_0.6B_Q8_0_DriveFirst.ipynb`；两份约 639MB Q8_0 GGUF 都必须通过 exact size + SHA256 + GGUF header 后才可直接使用。
- 状态边界：第四阶段 adapter/API/UI 已实现不等于权重已下载；在引导器实际跑完并重新扫描前，两张模型卡片继续显示准备引导器，不伪装为可执行。

- 基础设施继续推进：Drive package manifest v3 已加入。扫描器除权重外保留 <=16MiB 的 JSON/TXT/YAML/YML 运行支持文件，并在模型包中区分 `files` / `supportFiles` / `manifestFiles`；旧 v2 session 索引自动失效。
- Runtime v0.16 增加 `/v1/packages/status|materialize|stop`。它使用现有 DriveCache 断点缓存，按原相对路径恢复完整本地模型目录；大型权重优先硬链接，硬链接失败时拒绝偷偷复制几十 GB。
- Wan2.2 T2V/I2V Drive 审计确认：high/low-noise 都是 6 片 Diffusers safetensors + index/config 的目录格式，因此不能直接接 ComfyUI `UNETLoader`。下一步改为 Drive package materializer → managed Diffusers video worker，避免当前 ComfyUI 模板从外网重复下载几十/上百 GB。

## 完成定义

当用户在模型卡片上点击“使用模型”后，网页能自动完成检查、按需读取缓存、启动正确后端、提交任务并返回结果；如果模型当前不能运行，网页能在点击前准确说明缺少的权重、适配器或硬件条件。

