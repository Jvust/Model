# Model 0.19 检查记录

生成时间（UTC）：2026-09-28T14:05:31.218545+00:00
基线：f163f5cc9e76d7f13cf5f48a373727f16d98b365；本地代码不是已经推送的远端提交。

## 实际执行

- 基线Python测试：197通过。
- 当前Python测试：312通过；新增115项。使用 `python -m unittest discover -s tests`，真实SQLite/文件系统/线程/localhost HTTP；推理函数采用假工作器。
- JavaScript：所有assets下JS通过 `node --check`；model-capabilities和model-index测试通过。
- OAuth会话：14项fixture检查通过；mocked token endpoint，不是实际账号授权。
- 浏览器：Chromium离线DOM加载生产activation.js，测试专用fetch桥访问真实localhost HTTP。11项通过；推理是确定性假工作器。
- 明确失败记录：普通localhost导航被环境管理员策略阻断，`net::ERR_BLOCKED_BY_ADMINISTRATOR`。因此未宣称完整网页导航测试通过。替代DOM模式不规避用户服务权限，仅验证本地测试页面行为。
- PowerShell/Windows打包：已准备构建、签名检查、EXE smoke脚本和CI配置；此环境没有执行它们。
- 本轮真实GPU输出：0。用户Drive读取/下载/写入模型：0。远端GitHub代码提交：0。Drive新产物上传：0。

## 接续证据

浏览器2项任务：第1项seed123成功，第2项seed124模拟失败。重建Runtime后点接续，调用序列为 `[123,124,124]`，前一项未重复生成。测试同时检查参数恢复、结果持久化、HTML转义、390px宽屏无横向溢出以及无页面JS错误。

Python另覆盖取消后重启、来源内容变化、量化版本变化绑定、历史结果篡改、任务记录前后崩溃窗口、幂等重复按钮、并发Runtime所有权、分片漏失、错误HTTP Range、错误哈希、截断文件、符号链接和保留已完成缓存。

## 不能外推的结论

312个测试通过不意味着312个模型已能运行。GGUF/Qwen/FLUX新增图像路径的实机画质、速度、峰值显存、Windows安装和DriveFS断网召回均未评测。门槛只是配置限制，不能当成性能基准。

历史0.18 CI / 原生CPU模型证据保留于旧发布状态与原始产物；没有复制成0.19的真实模型结果。

## 证据文件

交付包 `evidence/` 包括Python/Node测试日志、activation-browser.json和截图，以及普通网页导航受策略阻断的原始失败日志。源码中的测试可以重跑。测试不是静态语法检查的替代说辞，而是确实运行过上述断言。
