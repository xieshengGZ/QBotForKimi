# 全局 Ticket 说明

更新时间：2026-11-02 13:56:46

## 1. 当前结论

**W1–W5 已完成（main 全量 271 tests）；W6 候选为 09、12，尚未开窗。**

- W4：04 `cbf6d7d`、07 `553d70e`、10 `a80e272`。
- W5：08 `eb5b3f6`（注入 worker）、11 `1da0a7e`（mirror pipeline/echo/batching）。
- 波次全量：`pytest tests/ -q` → 271 passed；`compileall -q kimico_gateway` 通过。
- W6 候选：09（control commands/status，09 依赖 06/08）、12（monitor loop，依赖 04/11）；两者文件面预计为 `input.py`+`router.py` 与 `__main__.py`/monitor，需开窗前再确认冲突。
- W7 候选：13（主进程接线）为大票，独占；之后 14、15、16（16 为最终验收大票）。

## 2. 波次表

| 波次 | 窗口 | Ticket | 标题 | 状态 | 分支/Commit | 验收 |
| ---- | ---- | ------ | ---- | ---- | ----------- | ---- |
| W1   | W1-1 | 01     | Walking skeleton — minimal config startup | 完成 | e9f535f | 6 tests |
| W2   | W2-1 | 02     | Complete configuration contract | 完成 | e2fb5c9 | 46 tests |
| W3   | W3-1 | 03     | tmux session lifecycle and pane state | 完成 | e76fa79 + 5583007 | 80 tests |
| W3   | W3-2 | 05     | Mirror state persistence | 完成 | 7ec7f28 | 71 tests |
| W3   | W3-3 | 06     | Outbound bus and sender | 完成 | 0d93c81 | 64 tests |
| W4   | W4-1 | 04     | Recovery state machine | 完成 | cbf6d7d | 135 tests |
| W4   | W4-2 | 07     | OneBot ingress and inbound queue | 完成 | 553d70e | 159 tests |
| W4   | W4-3 | 10     | Capture, overlap, and stability | 完成 | a80e272 | 160 tests；真实帧 60/60，p95 0.608s |
| W5   | W5-1 | 08     | Plain-text injection worker | 完成 | eb5b3f6 | 234 tests |
| W5   | W5-2 | 11     | Mirror pipeline, echo, and batching | 完成 | 1da0a7e | 245 tests |
| W6   | W6-1 | 09     | Control commands and status | 未开始 | - | - |
| W6   | W6-2 | 12     | Monitor loop integration | 未开始 | - | - |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 完成（01/02/05） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 完成（03/04） |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 进行中（07/08 完成；09 在 W6） |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 进行中（06/10/11 完成；12 在 W6） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 未开始 |

## 4. 开工顺序

1. **W6**：09（`input.py` 控制分支 + `router.py` 文案/解析/status）、12（monitor loop，预计 `__main__.py` + tests）。开窗前做冲突扫描；若 12 需要改 `capture.py`/`mirror.py`，与 09 无重叠，仍可并行。
2. W7：13（主进程接线）大票独占；它依赖 05/07/09/11/12。
3. W8：14（health/shutdown）；W9：15（systemd/NapCat）；W10：16（验收门禁，大票独占）。

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
| 2026-11-02 13:49 | W4   | W4-1 | 04     | 结束 | cbf6d7d | 135 tests；8 条清单全勾；main 全量 208 |
| 2026-11-02 13:49 | W4   | W4-2 | 07     | 结束 | 553d70e | 159 tests；8 条清单全勾；main 全量 208 |
| 2026-11-02 13:49 | W4   | W4-3 | 10     | 结束 | a80e272 | 160 tests；真实帧 60/60；main 全量 208 |
| 2026-11-02 13:51 | W5   | W5-1 | 08     | 开始 | feat/08-plain-injection | - |
| 2026-11-02 13:51 | W5   | W5-2 | 11     | 开始 | feat/11-mirror-pipeline | - |
| 2026-11-02 13:56 | W5   | W5-1 | 08     | 结束 | eb5b3f6 | 234 tests；9 条清单全勾；main 全量 271 |
| 2026-11-02 13:56 | W5   | W5-2 | 11     | 结束 | 1da0a7e | 245 tests；9 条清单全勾；main 全量 271 |

### 5.1 进行中

- 无
