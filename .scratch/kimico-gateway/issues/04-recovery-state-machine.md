# 04: Recovery state machine

**What to build:** An operator can rely on the gateway to respawn a confirmed dead pane and recreate a missing tmux session with bounded backoff, without ever killing a live kimico process or counting one failure twice. Repeated failures stop recovery and send one alarm.

**Blocked by:** 03 (tmux session lifecycle and pane state).

**Status:** done

- [x] A pane that dies after a long healthy run waits 5 seconds before the first respawn.
- [x] A gateway that starts with a dead pane also waits 5 seconds for the first attempt.
- [x] A process that dies within the success window records exactly one failure and moves to the next backoff step.
- [x] A failed respawn command records exactly one failure and is not counted again on the next round.
- [x] Reaching the configured failure maximum halts recovery and sends the alarm once.
- [x] A MISSING session calls recreate, not respawn; an UNKNOWN state performs no recovery action.
- [x] Recovery sleep can be interrupted by shutdown.
- [x] Restart notifications and the alarm use the router copy templates with the correct wait, attempt, window, and maximum values.

## Comments

**W4-1 · ticket 04 · 2026-11-02**

- Commit: `cbf6d7dad34503c29a946fa453cc95651f90ea1d` on `feat/04-recovery-state-machine` (`feat(session): recovery state machine (04)`)
- Files: `kimico_gateway/session.py`（新增 `RecoveryManager`）、`kimico_gateway/router.py`（仅两个文案模板 + `render_template`）、`tests/test_recovery.py`（新增 12 个测试）
- Commands and results (from `/home/ubuntu/qq/.worktrees/04`):
  - `/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_recovery.py -q` → 12 passed
  - `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` → 135 passed（原 123 + 新增 12）
  - `/home/ubuntu/qq/.venv/bin/python -m compileall -q kimico_gateway` → 通过
- 假设 / 说明：
  - 恢复 sleep 结束后重新查询 `pane_state`，并按查询到的当前状态执行（PRD §4 步骤 8–9）；若 MISSING 在等待期间变成 DEAD 则走 respawn，反之走 recreate；查询为 ALIVE 时直接返回 False，UNKNOWN 时不做动作也不记失败。
  - 滑动窗口按“剔除早于 `now - window_seconds` 的时间戳”实现，恰好等于边界的失败保留。
  - `sleep` 返回真值（生产传 `stop_event.wait`）视为停机打断：通知已发出，但不执行 respawn/recreate，返回 False。
  - `notify` 按契约由调用方保证不抛异常；本票未接线监控线程（ticket 12）与 `__main__`（ticket 13），因此端到端告警投递未覆盖，仅覆盖回调收到正确文案。
  - 未实现 `#` 指令解析、`#status` 及其他 router 常量，留给 ticket 09。
- 未完成项：无（本票范围内）；监控线程接线、真实 tmux 恢复演练不在本票。

### 控制器验收 · 2026-11-02

- 合并：`cbf6d7d`（`git merge --ff-only feat/04-recovery-state-machine`）。
- worktree：`pytest tests/ -q` → 135 passed（含 12 个 recovery 测试）；main 波次全量 208 passed；`compileall` 通过。
- 范围：`kimico_gateway/session.py`、新建最小 `kimico_gateway/router.py`（仅两条通知模板 + `render_template`）、`tests/test_recovery.py`；未改 `tests/test_session.py`、其他模块、PRD、全局文档；无 `.skip`/`xfail`、无运行时依赖。
- 关键点：首次/启动即死 5s；早死单次计数；respawn 失败不重复计；熔断单次告警；MISSING recreate、UNKNOWN 防御；sleep 真值中断；通知文案逐字匹配。
- 结论：8 条验收项全部通过，ticket 04 `done`。
