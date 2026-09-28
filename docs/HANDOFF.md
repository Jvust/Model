# Model 0.19 接手说明

## 当前正确状态

用户目标：选择模型即可自动准备并启用，之后可复用缓存、参数、输入和已完成任务继续。

基线是PR26分支 `feat/model-release-v017-20260927` 的 `f163f5cc9e76d7f13cf5f48a373727f16d98b365`。
目标分支 `feat/model-activation-resume-20260928` 已由前一轮创建并重新读回，仍在基线；本轮新代码**仅在交付物内，未推送**。

当前标记 `0.19-activation-rc1`。完整源码及模型引擎分开标识；候选包没有新编译的ModelRuntime.exe，不可调用旧0.18EXE后声称获得0.19。

## 实现位置

- activation_store.py：SQLite WAL/FULL、工作区、任务、进度和结果。
- activation_runtime.py：prepare/run/resume、幂等、收据恢复、逐项结果SHA。
- activation_backends.py：连接现有chat/task/native/image/video，严格白名单与硬件预检。
- package_integrity.py + drive_cache.py：索引逐分片、头部、版本、大小和哈希证据。
- runtime_variants.py + qwen_edit_workflow.py：显式Qwen2511量化副本及固定Comfy图。
- flux_edit_worker.py：4B蒸馏配置，分阶段文本编码器/denoiser。
- conversation_store.py + local_bridge.py：按模型来源版本隔离历史。
- instance_lock.py：多Runtime所有权；只有实际启动的服务器才能恢复任务。
- assets/activation.js：统一选择、输入、批量、保存、历史、接续及媒体读取。
- tools/run_candidate.ps1：D盘私有Python源码启动器。

## 下次必须先做

1. 查看 `governance/pending_sync.json`；恢复写入能力后同步交付变更到目标分支，基线变更则先对账。不要覆盖main或自动合并PR26。
2. 在干净Windows CI重跑全部测试、打包和EXE smoke；真实网站导航测试不能使用本轮受限环境的DOM替代结果冒充。
3. 在Y9000P RTX4060 8GB / 32GB RAM跑真实Drive冷缓存→取消→续传→热缓存、FLUX编辑、Qwen量化编辑；保留任务输入摘要、模型提交、输出、峰值VRAM/RAM。
4. 增补孤儿ComfyUI推理任务核销、所有后端供应链离线哈希锁；再按确切模型族扩展，不把未知safetensors标成可运行。

## 不改写的事实

本轮312项Python测试通过、14项OAuth fixture、11项受限浏览器DOM/HTTP检查通过。
GPU、Windows候选EXE、实际DriveFS、实际OAuth/双机HTTPS本轮未验收。所有权重及OAuth凭据都没有放入源码包。
