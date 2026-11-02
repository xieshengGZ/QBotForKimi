# 全局 Ticket 说明

更新时间：2026-11-02 14:12:35

## 1. 当前结论

**W7 完成：13（主进程接线与 status provider）已合入 main；main 全量 364 tests，compileall 通过。**

- 已完成 W1–W6（main 全量 350 tests）：01 `e9f535f`、02 `e2fb5c9`、03 `e76fa79+5583007`、05 `7ec7f28`、06 `0d93c81`、04 `cbf6d7d`、07 `553d70e`、10 `a80e272`、08 `eb5b3f6`、11 `1da0a7e`、09 `b6ad7a7`、12 `ee350d5`。
- W7：13 `8bdc4e7`（启动编排、Gateway、四线程、status provider、退出码 2/3/4；健康检查与优雅停机留给 14）。
- 波次全量：`pytest -q` → 364 passed；`compileall -q kimico_gateway` 通过。
- W8 候选：14（health/shutdown），依赖 13 已 done，可开窗。
- 之后：15（systemd/NapCat，独占，涉及真实部署）→ 16（验收门禁，大票独占）。

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

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 完成（01/02/05） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 完成（03/04） |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 完成（07/08/09） |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 完成（06/10/11/12） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 进行中（13 完成；14/15/16 待做） |

## 4. 开工顺序

1. **W8（下一波）**：14（health/shutdown）。依赖 13 已 done；与 15/16 串行。单票即可开 1 窗口，也可与无冲突票并行（当前 frontier 仅 14）。
2. W9：15（systemd/NapCat，独占，涉及真实部署与端口）；W10：16（验收门禁，大票独占）。
3. 波次规则不变：每波最多 3 窗口，大票不与他人同波；完整测试只在波次边界跑。

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

### 5.1 进行中

- 无
