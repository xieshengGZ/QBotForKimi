# 全局 Ticket 说明

更新时间：2026-11-02 13:51:18

## 1. 当前结论

**W1–W4 已完成（main 全量 208 tests）；W5 正在并行跑 ticket 08、11。**

- W4：04 `cbf6d7d`（RecoveryManager + 最小 router 模板）、07 `553d70e`（OneBot ingress + InboundQueue）、10 `a80e272`（capture，ADR-0001；真实帧回放 60/60，p95 0.608s）。
- W5-1 / 08：`input.py` 的 `inject` 与 plain-text worker；控制指令分支留给 09。08 对 mirror 类型使用 duck-typed 依赖，不 import 尚未存在的类。
- W5-2 / 11：`mirror.py` 的 MirrorSwitch/OutputEpoch/EchoSuppressor/chunk/MirrorPipeline；不改已有 OutboundBus/sender。
- W5 完成后 W6 候选：09（controller commands）、12（monitor loop）；13 大票随后独占。

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
| W5   | W5-1 | 08     | Plain-text injection worker | 进行中 | feat/08-plain-injection | 待窗口自测与控制器验收 |
| W5   | W5-2 | 11     | Mirror pipeline, echo, and batching | 进行中 | feat/11-mirror-pipeline | 待窗口自测与控制器验收 |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 完成（01/02/05） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 完成（03/04） |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 进行中（07 完成；08 在 W5；09 待做） |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 进行中（06/10 完成；11 在 W5；12 待做） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 未开始 |

## 4. 开工顺序

1. **W5**：08（input.py）、11（mirror.py）两个窗口并行。
2. W6：09（control commands/status）与 12（monitor loop）按依赖放开；可能需要先合并 11 再让 09 接线真实 mirror 类型。
3. W7：13（主进程接线）大票独占；之后 14、15、16 依次；16 为最终验收大票。

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

### 5.1 进行中

- **W5-1 / ticket 08**：Plain-text injection worker。
  - 票：`.scratch/kimico-gateway/issues/08-plain-injection.md`；Spec：`.scratch/03-inbound-control/spec.md`；PRD：`docs/prd/14-mod-input.md` §3–§8。
  - 分支 `feat/08-plain-injection`；worktree `.worktrees/08`。
  - 验收：9 条清单（FIFO 注入、原文、多行单次粘贴、唯一 buffer、`##`、空/空白、NUL、失败回执、成功静默）。
  - 注意：控制指令分支属 09；08 使用 duck-typed mirror 参数，不 import 尚未存在的 MirrorSwitch/OutputEpoch/EchoSuppressor。
- **W5-2 / ticket 11**：Mirror pipeline, echo, and batching。
  - 票：`.scratch/kimico-gateway/issues/11-mirror-pipeline.md`；Spec：`.scratch/04-output-mirror/spec.md`；PRD：`docs/prd/16-mod-mirror.md` §2/§7–§11。
  - 分支 `feat/11-mirror-pipeline`；worktree `.worktrees/11`。
  - 验收：9 条清单（开关、stale generation、过滤、行级回显、前缀、10s/50 上限、quiet/max flush、chunk、误删取舍）。
