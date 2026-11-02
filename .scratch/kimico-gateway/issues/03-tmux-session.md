# 03: tmux session lifecycle and pane state

**What to build:** An operator can let the gateway create a tmux session on first run or take over an existing one. The session has the configured history capacity and remain-on-exit behavior, and the gateway can report ALIVE, DEAD, MISSING, or UNKNOWN without confusing a transient tmux failure for a dead kimico process.

**Blocked by:** 02 (Complete configuration contract).

**Status:** done

- [x] Creating a session sets the history limit before the pane is created, then enables and verifies remain-on-exit.
- [x] Taking over an existing session enables and verifies remain-on-exit without creating a new session.
- [x] Taking over a session whose pane history is below the configured limit produces a warning rather than silently claiming success.
- [x] A session with an unexpected number of windows or panes fails startup with a clear error.
- [x] Pane state maps running, exited, missing, and unparseable/timeout results to ALIVE, DEAD, MISSING, and UNKNOWN respectively.
- [x] Every tmux command has a timeout and normalizes failures into a tmux error.
- [x] The public session API can be exercised against a real tmux server in a test harness without requiring the rest of the gateway.

## Comments

**W3-1 implementation (2026-11-02)**

- Commit: `e76fa79483231beb416e77de1cc850efe4207e1e` on `feat/03-tmux-session` (`feat(session): tmux session lifecycle and pane state (03)`)
- Files: `kimico_gateway/session.py`, `tests/test_session.py`

Commands and results (from `/home/ubuntu/qq/.worktrees/03`):

- `/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_session.py -q` → 31 passed
- `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` → 77 passed
- Real-tmux test repeated 3 times → 1 passed each run; the user's existing sessions (`kimi02`, `test`) were not touched

Assumptions / deviations:

- Pane/window targets use `=<session>:` rather than the PRD's literal `=<session>`. On tmux 3.4, `set-option -w -t =s` and `show-options -wv -t =s` fail with `no such window`, and `display-message -p -t =s` returns an empty format with rc 0 (which would map every pane to UNKNOWN). `has-session -t =s` still uses the literal exact-match form.
- Layout validation (acceptance 4) uses `display-message -p -t =<session>: '#{session_windows} #{window_panes}'`, since the PRD command table has no layout query.
- Create path assumes a running tmux server: tmux exits a server with no sessions, and a global option cannot be set before a server exists. The integration test therefore boots one bootstrap `bash` session on an isolated server (`TMUX_TMPDIR` under `tmp_path`, `TMUX` unset) and cleans it with `kill-server`; it uses the required `flock /tmp/kimico-gateway-integration.lock`, unique session names, and never targets a user session.
- All tmux calls use `subprocess.run` with list argv, no shell, `timeout=5`; `TimeoutExpired`/`OSError`/non-zero exits normalize to `TmuxError`. Timeout kills the direct tmux client, matching the spec's `subprocess.run` requirement; no separate process-group kill is performed.

Out of scope / not done: `RecoveryManager` and recovery tests (ticket 04), config orchestration, capture, input, OneBot, mirror.

**W3-1 follow-up: no-server first start (2026-11-02)**

- Commit: `55830075d534176ee1f1caf4d0e9f575218f762e` on `feat/03-tmux-session` (`fix(session): start tmux server for first-run session creation (03)`)
- Files: `kimico_gateway/session.py`, `tests/test_session.py`
- Fix: when `has-session` reports that no tmux server is running (`no server running` / `error connecting to`), `ensure_session` now creates a uniquely named bootstrap session (`kimico-gateway-bootstrap-<pid>-<random>`), then sets `history-limit`, creates the real session, and removes the bootstrap in a `finally` block. Cleanup is attempted on every failure path; the original `TmuxError` is preserved. A bootstrap cleanup failure is logged as a warning.
- New unit tests: bootstrap ordering (`has-session` → bootstrap `new-session` → `set-option -g history-limit` → real `new-session` → `kill-session`) and bootstrap cleanup when a later step fails.
- New real-tmux test: `test_first_start_creates_session_when_no_server_runs` starts with an isolated `TMUX_TMPDIR` and verifies `list-sessions` reports no server, then calls `ensure_session` directly. It asserts the real session becomes ALIVE, global `history-limit` and per-pane `#{history_limit}` are both `50000`, `remain-on-exit` is `on`, and the only remaining session is the real one (bootstrap removed). It keeps `flock /tmp/kimico-gateway-integration.lock`, unique names, and `kill-server` cleanup; user sessions are untouched.
- Commands and results (from `/home/ubuntu/qq/.worktrees/03`):
  - `/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_session.py -q` → 34 passed
  - `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` → 80 passed
  - Real no-server test repeated 3 times → 1 passed each run

This supersedes the earlier assumption that the create path needs an already-running tmux server; the integration harness no longer supplies a bootstrap session for the no-server case.

### 控制器验收 · 2026-11-02

- 合并：`e76fa79` + `5583007`（`git merge --ff-only feat/03-tmux-session`，main 快进）。
- 复核退回一次：无 tmux server 时首次启动 `set-option -g history-limit` 失败；窗口改用唯一 bootstrap session，并新增真实 tmux 的 no-server 首次创建测试。
- worktree：`pytest tests/ -q` → 80 passed（含 34 个 session 测试）；main 波次全量 123 passed；`compileall` 通过。
- 范围：仅 `kimico_gateway/session.py`、`tests/test_session.py`；无 `.skip`/`xfail`；无运行时第三方依赖；未改其他模块、PRD、全局文档。
- 偏差记录：tmux 3.4 的 window/pane 命令用 `=<session>:`（与 Step 0 capture 的结论一致）；布局校验用 `#{session_windows} #{window_panes}`。
- 结论：7 条验收项全部通过，ticket 03 `done`。
