# Specs

本目录按构建顺序存放 5 份 spec。每份 spec 都是 `Status: ready-for-agent`，可以直接交给实现代理。

1. `01-runtime-foundation/spec.md` — 配置、状态、日志、启动骨架
2. `02-tmux-session-recovery/spec.md` — tmux 会话、四态检测、恢复状态机
3. `03-inbound-control/spec.md` — OneBot 接入、入站队列、注入、控制指令
4. `04-output-mirror/spec.md` — 捕获、差分、稳定判定、回显、批处理、出站
5. `05-orchestration-deployment/spec.md` — 线程编排、健康检查、systemd、NapCat、验收

## Seams

| Spec | 测试 seam | 说明 |
|---|---|---|
| 01 | 公共 config/state API + 进程启动 | 不测私有解析函数；用临时 TOML/state 目录和子进程退出码验证 |
| 02 | `session` 公共 API + 注入 clock/sleep/tmux 回调的 `RecoveryManager` | 不依赖真实 tmux；用状态机场景表验证 |
| 03 | 原始 OneBot 事件 → 注入文本/控制回执 | 一个端到端 seam，fake tmux + fake outbound bus |
| 04 | 监控循环 → mirror 管道 → 出站总线 | fake capture + fake sender；纯算法函数单独做属性测试 |
| 05 | 完整进程 + fake tmux/NapCat | 进程级集成 + systemd 重启验收 |

## Tickets

The implementation tickets live in `kimico-gateway/issues/`, numbered in dependency order:

1. Walking skeleton — minimal config startup
2. Complete configuration contract
3. tmux session lifecycle and pane state
4. Recovery state machine
5. Mirror state persistence
6. Outbound bus and sender
7. OneBot ingress and inbound queue
8. Plain-text injection worker
9. Control commands and status
10. Capture, overlap, and stability
11. Mirror pipeline, echo, and batching
12. Monitor loop integration
13. Main process wiring and status provider
14. Health checks and graceful shutdown
15. systemd and NapCat deployment
16. Acceptance suite and real-kimico gate

## Notes

- 术语以根目录 `GLOSSARY.md` 为准。
- 本地 issue 文件使用 `Status:` / `Category:` 元数据。
- 每份 spec 的 Out of Scope 明确边界，避免实现代理跨 spec 扩张。
