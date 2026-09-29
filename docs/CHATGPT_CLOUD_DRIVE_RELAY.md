# ChatGPT → Google Drive 纯云端大文件中转

## 目标

解决这个具体问题：

```text
ChatGPT 生成 >512 MiB 文件
→ 无法作为普通聊天附件交付
→ 用户也不希望经过 Windows 本机
→ 最终希望直接进入 Google Drive
```

## 2026-09-29 实测结论

已经验证两条现有云端路径：

1. Google Drive 连接器可以直接接收 ChatGPT runtime 生成的正常大小文件并上传到 Drive。
2. 对一个 **520 MiB** 测试文件，连接器在把本地 sandbox 路径转换成 connector file reference 时失败：
   ```
   UNREGISTERED_FILE_REFERENCE
   ```
   因此现有 file-reference 路径不能直接解决 >512 MiB 交付问题。

另外，ChatGPT Library 已挂载 `/Google Drive`，普通模型生成文件可以直接 create-only 上传到 Drive；但 >512 MiB 文件不能先注册为 ChatGPT conversation/library file，所以仍卡在“生成物 → 可被连接器引用”这一步。

## 当前架构

本分支增加：

```text
workers/chatgpt-drive-upload-relay.mjs
```

其职责是：

```text
可访问的 HTTPS source URL
→ Cloud Worker
→ HTTP Range / streaming
→ Google Drive resumable upload
→ Drive file
```

文件不需要经过用户本机。

## API

### 健康检查

```
GET /health
```

### 从远端源流式上传到 Drive

```
POST /v1/upload/from-url
Authorization: Bearer <RELAY_SECRET>
Content-Type: application/json
```

请求示例：

```json
{
  "source_url": "https://temporary-object-store.example/file.zip?signature=...",
  "file_name": "artifact.zip",
  "mime_type": "application/zip",
  "parent_id": "optional-drive-folder-id"
}
```

默认 chunk：

```text
256 MiB
```

每个 chunk：

```text
source Range request
→ streaming body
→ Drive resumable PUT
```

不会把完整 1GB 文件读入 Worker 内存。

## 必需 secrets / bindings

```text
RELAY_SECRET
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
```

Google refresh token二选一：

```text
GOOGLE_REFRESH_TOKEN
```

或者复用现有 OAuth Worker：

```text
OAUTH_KV["refresh_token"]
```

必须设置：

```text
SOURCE_HOST_ALLOWLIST
```

示例：

```
storage.example.com,example.r2.cloudflarestorage.com
```

避免任意 URL SSRF。

## 当前真正的最后阻塞

这个 relay 已经解决：

```text
HTTPS 大文件源 → Google Drive
```

但还没有解决：

```text
ChatGPT 内部生成的 >512 MiB sandbox 文件
→ 可供云端 relay 读取的 HTTPS URL
```

截至当前 ChatGPT 自定义 MCP / Drive connector 的公开能力，没有发现一个可直接把“模型生成的超 512 MiB sandbox 文件”作为 file reference 交给自定义 MCP 的接口。

因此最终还差 **artifact ingress**。

## 推荐完成方式

最终形态：

```text
ChatGPT generation
→ temporary cloud object storage / signed HTTPS object
→ chatgpt-drive-upload-relay
→ Google Drive resumable upload
→ Drive Desktop（用户端自然同步）
```

### 优先方案

让“生成大文件”的任务从一开始就在云端 worker/job 中执行，直接把输出写入对象存储或 Drive，而不是先在 ChatGPT sandbox 生成完整 1GB 文件。

这样完全避开 512 MiB conversation attachment 注册层。

## 为什么不把本地 Tunnel 当最终方案

用户要求纯云端，而且已有 Google Drive Desktop。

因此：

- Windows tunnel 仅保留调试/兼容用途。
- 正式大文件交付不经过用户电脑。
- Google Drive 作为最终交付面。
- 大文件生成和传输都应发生在云端。

## 下一步

1. 部署本 Worker 到现有 Cloudflare Worker 环境。
2. 配置 secrets / OAUTH_KV。
3. 用一个 >512 MiB、支持 Range 的 signed URL 做真实上传测试。
4. 验证 Drive 最终 size/hash。
5. 继续解决 ChatGPT artifact ingress：优先寻找官方可用的 file reference / temporary URL；如果没有，则把大文件生成任务迁移到云端 job，使文件从一开始就不落 ChatGPT sandbox。
