# 全局 Ticket 说明

更新时间：2026-11-02 13:26:21

## 1. 当前结论

**Step 0 已通过；W1、W2 完成；W3 正在并行跑 ticket 03、05、06。**

- W1：ticket 01 `e9f535f`，6 个验收测试通过。
- W2：ticket 02 `e2fb5c9`，46 个测试通过，完整配置契约落地。
- W3：03（session.py）、05（state.py）、06（outbound bus/sender）三个窗口；文件面不重叠。03 的真实 tmux 测试需 flock。
- W3 完成后 W4 候选：10（capture/stability，按 ADR-0001）；以及 03/05/06 的未完成项。
- Step 0 门禁产物：`docs/output/spike/kimico-capture.md`；捕获契约：`docs/adr/0001-redraw-aware-capture.md`。

## 2. 波次表

| 波次 | 窗口 | Ticket | 标题 | 状态 | 分支/Commit | 验收 |
| ---- | ---- | ------ | ---- | ---- | ----------- | ---- |
| W1   | W1-1 | 01     | Walking skeleton — minimal config startup | 完成 | e9f535f | pytest 6 passed；compileall；树未变 |
| W2   | W2-1 | 02     | Complete configuration contract | 完成 | e2fb5c9 | pytest 46 passed；compileall；树未变 |
| W3   | W3-1 | 03     | tmux session lifecycle and pane state | 进行中 | feat/03-tmux-session | 待窗口自测与控制器验收 |
| W3   | W3-2 | 05     | Mirror state persistence | 进行中 | feat/05-mirror-state | 待窗口自测与控制器验收 |
| W3   | W3-3 | 06     | Outbound bus and sender | 进行中 | feat/06-outbound-bus | 待窗口自测与控制器验收 |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 进行中（01/02 完成；05 在 W3） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 进行中（03 在 W3；04 待做） |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 未开始 |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 进行中（06 在 W3；捕获见 ADR-0001） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 未开始 |

## 4. 开工顺序

1. **W3**：03、05、06 三个窗口并行；03 的真实 tmux 测试用 `flock /tmp/kimico-gateway-integration.lock`，05/06 纯单测。
2. W4：10（capture/stability，按 ADR-0001 实现重绘感知差分）；若 W3 有票未过，先续做。
3. 之后按依赖继续 07（OneBot ingress）、08（注入）、11（mirror pipeline）、09（router）、12（monitor）、14（health）、15（systemd）、16（验收）；大票 13/16 独占。

## 5. 执行日志

| 时间             | 波次 | 窗口 | Ticket | 事件 | 分支/Commit | 验收 |
| ---------------- | ---- | ---- | ------ | ---- | ----------- | ---- |
| 2026-11-02 12:42 | -    | -    | -      | 预检阻塞 | - | 缺少 Step 0 产物，kimico 不在 PATH |
| 2026-11-02 12:44 | -    | -    | -      | 开始 Step 0 | - | kimi 2.4.1 / tmux 3.4 可用 |
| 2026-11-02 12:48 | -    | -    | -      | 采样器首跑作废 | - | `-t =kimico` 无效，0 轮有效 capture |
| 2026-11-02 13:00 | -    | -    | -      | Step 0 初判 | - | **不通过**：v1.1 差分 210/210 回答行丢失 |
| 2026-11-02 13:18 | -    | -    | -      | Step 0 复判 | 85a721d | **通过**：v1.2 重绘感知差分 210/210 送达，p95 0.601s |
| 2026-11-02 13:18 | W1   | W1-1 | 01     | 开始 | feat/01-walking-skeleton | - |
| 2026-11-02 13:21 | W1   | W1-1 | 01     | 结束 | e9f535f | pytest 6 passed；compileall；树未变；5 条清单全勾 |
| 2026-11-02 13:21 | W2   | W2-1 | 02     | 开始 | feat/02-config-contract | - |
| 2026-11-02 13:25 | W2   | W2-1 | 02     | 结束 | e2fb5c9 | pytest 46 passed；compileall；树未变；8 条清单全勾 |
| 2026-11-02 13:26 | W3   | W3-1 | 03     | 开始 | feat/03-tmux-session | - |
| 2026-11-02 13:26 | W3   | W3-2 | 05     | 开始 | feat/05-mirror-state | - |
| 2026-11-02 13:26 | W3   | W3-3 | 06     | 开始 | feat/06-outbound-bus | - |

### 5.1 进行中

- **W3-1 / ticket 03**：tmux session lifecycle and pane state。
  - 票：`.scratch/kimico-gateway/issues/03-tmux-session.md`；Spec：`.scratch/02-tmux-session-recovery/spec.md`；PRD：`docs/prd/17-mod-session.md`。
  - 分支 `feat/03-tmux-session`；worktree `.worktrees/03`。
  - 验收：7 条清单（history 先于 new-session、接管幂等 remain-on-exit、低 history warning、窗口/pane 数量异常报错、四态映射、所有 tmux 命令超时归一化、真实 tmux 可用）。
- **W3-2 / ticket 05**：Mirror state persistence。
  - 票：`.scratch/kimico-gateway/issues/05-mirror-state.md`；Spec：`.scratch/01-runtime-foundation/spec.md`；PRD：`docs/prd/11-mod-state.md`、`docs/prd/03-state.md`。
  - 分支 `feat/05-mirror-state`；worktree `.worktrees/05`。
  - 验收：8 条清单（缺失/损坏/类型回退、额外键忽略、原子写 + fsync、并发、失败不抛）。
- **W3-3 / ticket 06**：Outbound bus and sender。
  - 票：`.scratch/kimico-gateway/issues/06-outbound-bus.md`；Spec：`.scratch/04-output-mirror/spec.md`；PRD：`docs/prd/16-mod-mirror.md` §2/§5/§6/§11、`docs/prd/12-mod-onebot.md` §5。
  - 分支 `feat/06-outbound-bus`；worktree `.worktrees/06`。
  - 验收：8 条清单（控制优先、输出溢出丢最旧 + 单条提示、控制不丢、generation 失效、重试一次 2s、间隔、失败日志只含长度/原因）。
