# 全局 Ticket 说明

更新时间：2026-11-02 13:34:01

## 1. 当前结论

**Step 0 已通过；W1、W2、W3 全部完成；main 全量 123 个测试通过。**

- W1：ticket 01 `e9f535f`（6 tests）。
- W2：ticket 02 `e2fb5c9`（完整配置契约）。
- W3：ticket 03 `e76fa79` + `5583007`（session，含 no-server 首次启动修复）、05 `7ec7f28`（state）、06 `0d93c81`（outbound bus/sender）。
- 波次全量：`pytest tests/ -q` → 123 passed；`compileall -q kimico_gateway` 通过。
- 下一波 W4 候选：04（RecoveryManager）、07（OneBot ingress）、10（capture/stability）——三者文件面不重叠，可并行；10 必须按 `docs/adr/0001-redraw-aware-capture.md` 实现。
- 捕获门禁：`docs/output/spike/kimico-capture.md`（v1.2，210/210 行送达，p95 0.601s）。

## 2. 波次表

| 波次 | 窗口 | Ticket | 标题 | 状态 | 分支/Commit | 验收 |
| ---- | ---- | ------ | ---- | ---- | ----------- | ---- |
| W1   | W1-1 | 01     | Walking skeleton — minimal config startup | 完成 | e9f535f | 6 tests；compileall；树未变 |
| W2   | W2-1 | 02     | Complete configuration contract | 完成 | e2fb5c9 | 46 tests；compileall；树未变 |
| W3   | W3-1 | 03     | tmux session lifecycle and pane state | 完成 | e76fa79 + 5583007 | 80 tests；no-server 真实 tmux 测试；main 全量 123 |
| W3   | W3-2 | 05     | Mirror state persistence | 完成 | 7ec7f28 | 71 tests；main 全量 123 |
| W3   | W3-3 | 06     | Outbound bus and sender | 完成 | 0d93c81 | 64 tests；main 全量 123 |
| W4   | W4-1 | 04     | Recovery state machine | 未开始 | - | - |
| W4   | W4-2 | 07     | OneBot ingress and inbound queue | 未开始 | - | - |
| W4   | W4-3 | 10     | Capture, overlap, and stability | 未开始 | - | 必须遵循 ADR-0001 |

## 3. Spec/里程碑

| Spec | 范围 | 状态 |
| ---- | ---- | ---- |
| 01-runtime-foundation | 配置、状态、日志、启动骨架 | 完成（01/02/05） |
| 02-tmux-session-recovery | tmux 会话、四态检测、恢复状态机 | 进行中（03 完成；04 待做） |
| 03-inbound-control | OneBot 接入、入站队列、注入、控制指令 | 未开始（07 在 W4） |
| 04-output-mirror | 捕获、差分、稳定判定、回显、批处理、出站 | 进行中（06 完成；10/11 待做） |
| 05-orchestration-deployment | 线程编排、健康检查、systemd、NapCat、验收 | 未开始 |

## 4. 开工顺序

1. **W4**：04（session.py 的 RecoveryManager）、07（onebot.py 的 HTTP ingress）、10（capture.py，按 ADR-0001）三个窗口并行。
2. 04/07/10 落地后：08（注入）、11（mirror pipeline）、09（router）按依赖放开；12、14、15、16 随后；大票 13/16 独占。
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

### 5.1 进行中

- 无
