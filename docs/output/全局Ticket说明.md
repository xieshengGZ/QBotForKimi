# 全局 Ticket 说明

更新时间：2026-11-02 13:39:54

## 1. 当前结论

**W1–W3 已完成（main 全量 123 tests）；W4 正在并行跑 ticket 04、07、10。**

- W4-1 / 04：RecoveryManager 加入 `session.py`；同时创建最小 `router.py`（仅重启通知/告警模板 + `render_template`），供 ticket 09 后续扩展。
- W4-2 / 07：`onebot.py` HTTP 接入 + `input.py` 的 `InboundQueue`；注入 worker 留给 08。
- W4-3 / 10：`capture.py` 按 `docs/adr/0001-redraw-aware-capture.md` 实现重绘感知差分；不得回退到 v1.1 append-only 主路径。
- Step 0 门禁：`docs/output/spike/kimico-capture.md`（v1.2，210/210 行送达，p95 0.601s）。

## 2. 波次表

| 波次 | 窗口 | Ticket | 标题 | 状态 | 分支/Commit | 验收 |
| ---- | ---- | ------ | ---- | ---- | ----------- | ---- |
| W1   | W1-1 | 01     | Walking skeleton — minimal config startup | 完成 | e9f535f | 6 tests |
| W2   | W2-1 | 02     | Complete configuration contract | 完成 | e2fb5c9 | 46 tests |
| W3   | W3-1 | 03     | tmux session lifecycle and pane state | 完成 | e76fa79 + 5583007 | 80 tests |
| W3   | W3-2 | 05     | Mirror state persistence | 完成 | 7ec7f28 | 71 tests |
| W3   | W3-3 | 06     | Outbound bus and sender | 完成 | 0d93c81 | 64 tests |
| W4   | W4-1 | 04     | Recovery state machine | 进行中 | feat/04-recovery-state-machine | 待窗口自测与控制器验收 |
| W4   | W4-2 | 07     | OneBot ingress and inbound queue | 进行中 | feat/07-onebot-ingress | 待窗口自测与控制器验收 |
| W4   | W4-3 | 10     | Capture, overlap, and stability | 进行中 | feat/10-capture-stability | 待窗口自测与控制器验收；遵循 ADR-0001 |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 完成（01/02/05） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 进行中（03 完成；04 在 W4） |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 进行中（07 在 W4；08/09 待做） |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 进行中（06 完成；10 在 W4；11 待做） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 未开始 |

## 4. 开工顺序

1. **W4**：04、07、10 三个窗口并行；文件面为 `session.py`/`router.py`、`onebot.py`/`input.py`、`capture.py`，互不重叠。
2. W4 落地后：08（注入）、11（mirror pipeline）、09（router）按依赖放开；12、14、15、16 随后；大票 13/16 独占。
3. 波次全量、部署物与真实 TUI 回归按 `docs/prd/40-build-order.md` 执行。

## 5. 执行日志

| 时间             | 波次 | 窗口 | Ticket | 事件 | 分支/Commit | 验收 |
| ---------------- | ---- | ---- | ------ | ---- | ----------- | ---- |
| 2026-11-02 12:42 | -    | -    | -      | 预检阻塞 | - | 缺少 Step 0 产物，kimico 不在 PATH |
| 2026-11-02 12:44 | -    | -    | -      | 开始 Step 0 | - | kimi 2.4.1 / tmux 3.4 可用 |
| 2026-11-02 12:48 | -    | -    | -      | 采样器首跑作废 | - | `-t =kimico` 无效，0 轮有效 capture |
| 2026-11-02 13:00 | -    | -    | -      | Step 0 初判 | - | **不通过**：v1.1 差分 210/210 回答行丢失 |
| 2026-11-02 13:18 | -    | -    | -      | Step 0 复判 | 85a721d | **通过**：v1.2 重绘感知差分 210/210 送达，p95 0.601s |
| 2026-11-02 13:18 | W1   | W1-1 | 01     | 开始 | feat/01-walking-skeleton | - |
| 2026-11-02 13:21 | W1   | W1-1 | 01     | 结束 | e9f535f | 6 tests；5 条清单全勾 |
| 2026-11-02 13:21 | W2   | W2-1 | 02     | 开始 | feat/02-config-contract | - |
| 2026-11-02 13:25 | W2   | W2-1 | 02     | 结束 | e2fb5c9 | 46 tests；8 条清单全勾 |
| 2026-11-02 13:26 | W3   | W3-1 | 03     | 开始 | feat/03-tmux-session | - |
| 2026-11-02 13:26 | W3   | W3-2 | 05     | 开始 | feat/05-mirror-state | - |
| 2026-11-02 13:26 | W3   | W3-3 | 06     | 开始 | feat/06-outbound-bus | - |
| 2026-11-02 13:30 | W3   | W3-1 | 03     | 退回修复 | 5583007 | no-server 首次启动缺口 |
| 2026-11-02 13:34 | W3   | W3-1 | 03     | 结束 | e76fa79+5583007 | 7 条清单全勾；main 全量 123 |
| 2026-11-02 13:34 | W3   | W3-2 | 05     | 结束 | 7ec7f28 | 8 条清单全勾；main 全量 123 |
| 2026-11-02 13:34 | W3   | W3-3 | 06     | 结束 | 0d93c81 | 8 条清单全勾；main 全量 123 |
| 2026-11-02 13:40 | W4   | W4-1 | 04     | 开始 | feat/04-recovery-state-machine | - |
| 2026-11-02 13:40 | W4   | W4-2 | 07     | 开始 | feat/07-onebot-ingress | - |
| 2026-11-02 13:40 | W4   | W4-3 | 10     | 开始 | feat/10-capture-stability | - |

### 5.1 进行中

- **W4-1 / ticket 04**：Recovery state machine。
  - 票：`.scratch/kimico-gateway/issues/04-recovery-state-machine.md`；Spec：`.scratch/02-tmux-session-recovery/spec.md`；PRD：`docs/prd/17-mod-session.md` §4–8、`docs/prd/13-mod-router.md` §2–3。
  - 分支 `feat/04-recovery-state-machine`；worktree `.worktrees/04`。
  - 验收：8 条清单（首次/启动即死 5s、单次失败计数、respawn 失败不重复计、熔断告警一次、MISSING recreate、UNKNOWN 不动作、sleep 可中断、路由模板文案）。
- **W4-2 / ticket 07**：OneBot ingress and inbound queue。
  - 票：`.scratch/kimico-gateway/issues/07-onebot-ingress.md`；Spec：`.scratch/03-inbound-control/spec.md`；PRD：`docs/prd/12-mod-onebot.md` §3–9、`docs/prd/14-mod-input.md` §2。
  - 分支 `feat/07-onebot-ingress`；worktree `.worktrees/07`。
  - 验收：8 条清单（合法事件、401、各状态码、413 close、静默丢弃、文本拼接、队列满 busy、单线程顺序）。
- **W4-3 / ticket 10**：Capture, overlap, and stability。
  - 票：`.scratch/kimico-gateway/issues/10-capture-stability.md`；Spec：`.scratch/04-output-mirror/spec.md`；PRD：`docs/prd/15-mod-capture.md`（§4/§6 由 ADR 取代）；决策：`docs/adr/0001-redraw-aware-capture.md`；门禁：`docs/output/spike/kimico-capture.md`。
  - 分支 `feat/10-capture-stability`；worktree `.worktrees/10`。
  - 验收：8 条清单 + ADR-0001（`=<session>:`、稳定前缀 + 活尾同步、半行替换、无重复、60+ 行流式、5000 行线性预算）。
