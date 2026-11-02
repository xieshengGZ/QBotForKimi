# 全局 Ticket 说明

更新时间：2026-11-02 13:21:39

## 1. 当前结论

**Step 0 已通过；W1（ticket 01）已完成；W2 正在跑 ticket 02。**

- W1：ticket 01 已合并 `e9f535f`，6 个验收测试通过，树比对无差异。
- W2：ticket 02（Complete configuration contract），窗口 W2-1，分支 `feat/02-config-contract`，worktree `.worktrees/02`。
- Step 0 门禁产物：`docs/output/spike/kimico-capture.md`（v1.2 重绘感知差分，210/210 行送达，p95 0.601s）。
- 捕获契约：`docs/adr/0001-redraw-aware-capture.md`。

## 2. 波次表

| 波次 | 窗口 | Ticket | 标题 | 状态 | 分支/Commit | 验收 |
| ---- | ---- | ------ | ---- | ---- | ----------- | ---- |
| W1   | W1-1 | 01     | Walking skeleton — minimal config startup | 完成 | e9f535f | pytest 6 passed；compileall；树未变 |
| W2   | W2-1 | 02     | Complete configuration contract | 进行中 | feat/02-config-contract | 待窗口自测与控制器验收 |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 进行中（ticket 01 完成；02/05 待做） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 未开始 |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 未开始 |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 未开始（捕获契约已由 ADR-0001 修订） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 未开始 |

## 4. 开工顺序

1. **W2**：ticket 02（当前唯一候选），窗口 W2-1。
2. ticket 02 落地后：03、05、06、10 解锁，W3 最多 3 个窗口（按 NN 顺序与冲突扫描；10 涉及 capture 契约，注意与 ADR-0001 对齐）。
3. 之后按依赖继续 07/08/11/09/12/14/15/16；大票 13/16 独占。

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

### 5.1 进行中

- **W2-1 / ticket 02**：Complete configuration contract。
  - 票：`.scratch/kimico-gateway/issues/02-config-contract.md`
  - Spec：`.scratch/01-runtime-foundation/spec.md`
  - PRD：`docs/prd/10-mod-config.md`、`docs/prd/02-config.md`
  - 分支：`feat/02-config-contract`；worktree：`.worktrees/02`
  - 窗口开始时间：2026-11-02 13:21:39
  - 验收：8 条清单（全部字段/默认值、类型/范围/正则/路径聚合错误、空/占位 token 拒绝、token 打码、未知键 warning、非回环 warning、不可变、尾部斜杠与日志级别归一化）
