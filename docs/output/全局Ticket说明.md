# 全局 Ticket 说明

更新时间：2026-11-02 15:25:30

## 1. 当前结论

**全部 16 张票完成。W10 验收通过：假环境 20 PASS / 0 FAIL / 3 SKIP（另有 systemd 3 PASS、真实 kimi PASS）；main 全量 403 tests，compileall 通过。**

- W1–W9 全部 done；W10：16 `fd77f51`（`acceptance/**` 套件、systemd 检查、真实 kimi 门禁）。
- 真实 kimi 门禁：338.33s，39/39 标签送达，0 丢失，0 重复回答行，p95 1.662s（≤2s），capture 错误 0；同时作为最终 Step 0 回归。
- 验收报告：`docs/output/acceptance-report.md`；部署验证：`docs/output/deploy-verification.md`。
- 已知限制：真实 NapCat/QQ 联调未做（端口 3000 被无关容器占用、无 QQ 账号）；长提示软换行续行可能仍被镜像（非回答丢失，报告 §4.2）。

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
| W6   | W6-1 | 09     | Control commands and status | 完成 | b6ad7a7 | 67 tests；8 条清单全勾；main 全量 350 |
| W6   | W6-2 | 12     | Monitor loop integration | 完成 | ee350d5 | 12 tests；8 条清单全勾；main 全量 350 |
| W7   | W7-1 | 13     | Main process wiring and status provider | 完成 | 8bdc4e7 | 14 tests；8 条清单全勾；main 全量 364 |
| W8   | W8-1 | 14     | Health checks and graceful shutdown | 完成 | 0258a54 | 6 tests；7 条清单全勾；main 全量 370 |
| W9   | W9-1 | 15     | systemd and NapCat deployment | 完成 | 5cbc25b | 18 tests；7 条清单全勾；真实 systemd PID 不变；main 全量 388 |
| W10  | W10-1 | 16    | Acceptance suite and real-kimico gate | 完成 | fd77f51 | 验收 20 PASS/0 FAIL/3 SKIP；systemd 3 PASS；真实 kimi 39/39，p95 1.662s；main 全量 403 |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 完成（01/02/05） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 完成（03/04） |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 完成（07/08/09） |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 完成（06/10/11/12） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 完成（13/14/15/16） |

## 4. 开工顺序

1. **无剩余 ticket**：16 张票全部 done，main 为最终交付树。
2. 后续仅剩环境相关验证：真实 NapCat + QQ 联调（需空闲 3000 端口与 `主QQ账号`）、长提示软换行回显的后续改进。
3. 构建顺序以 `docs/prd/40-build-order.md` 为准，未改写。

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
| 2026-11-02 13:59 | W6   | W6-1 | 09     | 开始 | feat/09-control-commands | - |
| 2026-11-02 13:59 | W6   | W6-2 | 12     | 开始 | feat/12-monitor-loop | - |
| 2026-11-02 14:03 | W6   | W6-1 | 09     | 结束 | b6ad7a7 | 67 tests；8 条清单全勾；树比对一致；main 全量 350 |
| 2026-11-02 14:04 | W6   | W6-2 | 12     | 结束 | ee350d5 | 12 tests；8 条清单全勾；树比对一致；main 全量 350；compileall ok |
| 2026-11-02 14:05 | W7   | W7-1 | 13     | 开始 | feat/13-main-wiring | - |
| 2026-11-02 14:12 | W7   | W7-1 | 13     | 结束 | 8bdc4e7 | 14 tests；8 条清单全勾；树比对一致；main 全量 364；compileall ok |
| 2026-11-02 14:13 | W8   | W8-1 | 14     | 开始 | feat/14-health-shutdown | - |
| 2026-11-02 14:18 | W8   | W8-1 | 14     | 结束 | 0258a54 | 6 tests；7 条清单全勾；树比对一致；main 全量 370；compileall ok |
| 2026-11-02 14:20 | W9   | W9-1 | 15     | 开始 | feat/15-systemd-napcat | - |
| 2026-11-02 14:27 | W9   | W9-1 | 15     | 结束 | 5cbc25b | 18 tests；7 条清单全勾；真实 systemd PID 不变；main 全量 388；compileall/scripts ok |
| 2026-11-02 14:30 | W10  | W10-1 | 16    | 开始 | feat/16-acceptance-gate | - |
| 2026-11-02 15:25 | W10  | W10-1 | 16    | 结束 | fd77f51 | 20 PASS/0 FAIL/3 SKIP；systemd 3 PASS；真实 kimi 39/39，p95 1.662s；main 全量 403；compileall ok |

### 5.1 进行中

- 无
