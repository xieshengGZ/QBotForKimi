# 全局 Ticket 说明

更新时间：2026-11-02 13:18:44

## 1. 当前结论

**Step 0 真实 kimico TUI 门禁已通过（v1.2 重绘感知差分），W1 已开始。**

- 门禁产物：`docs/output/spike/kimico-capture.md`（结论：通过）。
- 实测：5.4 分钟、1083 轮；210/210 条带标签长回答行送达，0 丢失；p95 延迟 0.601s；capture 错误 0；粘贴一次提交、无重复。
- 方案：稳定公共前缀 + 活尾同步（ADR-0001），保留 KMP overlap 处理窗口滑动，`recover_new_lines` 作最后回退。
- 附带修正：`capture-pane -t =<session>` 在 tmux 3.4 下不可用，真实写法为 `=<session>:`；ticket 10 必须采用。
- 当前 W1：ticket 01（Walking skeleton），窗口 W1-1，分支 `feat/01-walking-skeleton`，worktree `.worktrees/01`。

## 2. 波次表

| 波次 | 窗口 | Ticket | 标题 | 状态 | 分支/Commit | 验收 |
| ---- | ---- | ------ | ---- | ---- | ----------- | ---- |
| W1   | W1-1 | 01     | Walking skeleton — minimal config startup | 进行中 | feat/01-walking-skeleton | 待窗口自测与控制器验收 |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 进行中（ticket 01） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 未开始 |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 未开始 |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 未开始（捕获契约已由 ADR-0001 修订） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 未开始 |

## 4. 开工顺序

1. **W1**：ticket 01（当前唯一候选），窗口 W1-1。
2. ticket 01 落地验收后：ticket 02（Complete configuration contract）解锁，W2 单独跑它。
3. 之后按依赖顺序放开 03/05/06/10（每波最多 3 个窗口；大票 13/16 独占），再进入 07/08/11/09/12/14/15/16。

## 5. 执行日志

| 时间             | 波次 | 窗口 | Ticket | 事件 | 分支/Commit | 验收 |
| ---------------- | ---- | ---- | ------ | ---- | ----------- | ---- |
| 2026-11-02 12:42 | -    | -    | -      | 预检阻塞 | - | 缺少 Step 0 产物，kimico 不在 PATH |
| 2026-11-02 12:44 | -    | -    | -      | 开始 Step 0 | - | kimi 2.4.1 / tmux 3.4 可用 |
| 2026-11-02 12:48 | -    | -    | -      | 采样器首跑作废 | - | `-t =kimico` 无效，0 轮有效 capture |
| 2026-11-02 13:00 | -    | -    | -      | Step 0 初判 | - | **不通过**：v1.1 差分 210/210 回答行丢失 |
| 2026-11-02 13:18 | -    | -    | -      | Step 0 复判 | 85a721d | **通过**：v1.2 重绘感知差分 210/210 送达，p95 0.601s |
| 2026-11-02 13:18 | W1   | W1-1 | 01     | 开始 | feat/01-walking-skeleton | - |

### 5.1 进行中

- **W1-1 / ticket 01**：Walking skeleton — minimal config startup。
  - 票：`.scratch/kimico-gateway/issues/01-walking-skeleton.md`
  - Spec：`.scratch/01-runtime-foundation/spec.md`
  - 分支：`feat/01-walking-skeleton`；worktree：`.worktrees/01`
  - 窗口开始时间：2026-11-02 13:18:44
  - 验收：5 条清单（启动日志格式、SIGTERM/SIGINT 退出码 0、缺字段退出码 2 并逐行列出、TOML 语法错误退出码 2、运行期改配置不生效）
