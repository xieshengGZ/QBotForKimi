# 13: Main process wiring and status provider

**What to build:** One process starts the gateway in the documented order: load configuration, preflight the kimico command, create or take over the tmux session, construct the components, bind the HTTP server, start the HTTP/input/monitor/sender threads, and provide the eight-line status snapshot. Configuration, preflight/tmux, and bind failures exit with the correct codes.

**Blocked by:** 05 (Mirror state persistence), 07 (OneBot ingress and inbound queue), 09 (Control commands and status), 11 (Mirror pipeline, echo, and batching), 12 (Monitor loop integration).

**Status:** done

- [x] A valid startup loads configuration and state, preflights kimico, ensures the tmux session, binds the HTTP server, and starts all four threads.
- [x] A missing kimico command exits with code 3 and a clear stderr message before starting threads.
- [x] A tmux self-check failure exits with code 3.
- [x] An HTTP bind/port failure exits with code 4.
- [x] A configuration error exits with code 2.
- [x] The status provider returns the eight documented fields and a bounded pane-state query.
- [x] `#status` works through the real wiring with fake tmux and fake outbound transport.
- [x] Runtime configuration edits do not alter the running process.

## Comments

- W7-1 implementation complete.
- Commit: `8bdc4e70b69c608f25899e39955ee831c86ce726` (`feat(main): wire startup and status provider (13)`).
- Files: `kimico_gateway/__main__.py` (startup sequence, `Gateway`, injectable `run_gateway`, `make_status_provider`, optional monitor capture/pane seams; `RuntimeState` and monitor behaviour preserved), `tests/test_main_wiring.py` (14 new tests), `tests/test_walking_skeleton.py` (fake `kimico`/`tmux` on PATH plus readiness wait; original log-format, signal, config-not-reloaded and config-error intentions retained).
- Verification (run in `/home/ubuntu/qq/.worktrees/13` with `/home/ubuntu/qq/.venv/bin/python`):
  - `-m pytest tests/test_main_wiring.py tests/test_walking_skeleton.py tests/test_monitor_loop.py -q` → 32 passed in 2.15s.
  - `-m pytest -q` → 364 passed in 20.89s.
  - `-m compileall -q kimico_gateway` → ok.
- Implementation notes:
  - Startup order is state load → kimico `shlex.split`/`shutil.which` preflight → `ensure_session` → component assembly → `OnebotServer` bind → four daemon threads → SIGTERM/SIGINT handlers → `stop_event.wait()`. Exit codes: config 2 in `main()`, preflight/tmux 3, bind 4, normal stop 0.
  - Thread naming is `gateway-http`, `gateway-input`, `gateway-monitor`, `gateway-sender`; `on_ready` is the test seam that exposes the assembled `Gateway`.
  - `make_status_provider` queries pane state on every call, maps ALIVE/DEAD/MISSING/UNKNOWN as documented, and degrades a raised query to UNKNOWN with an ERROR log.
  - `#status` is exercised end-to-end through `run_gateway` with fake pane state, fake capture, fake HTTP server and a monkeypatched `send_private_msg`.
- Unfinished / assumptions:
  - Health-check thread-death detection and exit code 5, thread joins, HTTP `shutdown()`, final `state.save()`, and other graceful-shutdown steps are intentionally left to ticket 14 per the ticket scope. `Gateway.wait()` only waits for `stop_event` and returns 0; it does not touch tmux/kimico.
  - `run_gateway` accepts `which`, `ensure_session_fn`, `respawn_pane_fn`, `get_pane_state_fn`, `capture_pane_fn`, `server_factory`, `thread_factory`, `state_store`, `install_signal_handlers` and `on_ready` for dependency injection; production defaults are the real modules.
  - Walking-skeleton tests now use a fake `tmux` that reports an existing ALIVE session and a fake `kimico`; no real tmux server or systemd is used by the new wiring tests.

- 2026-11-02 W7 controller: verified the tested tree is byte-identical for `kimico_gateway/` and `tests/`, merged `8bdc4e7` (feature) to main with `git merge --ff-only`. Wave-end on main: `/home/ubuntu/qq/.venv/bin/python -m pytest -q` → 364 passed; `/home/ubuntu/qq/.venv/bin/python -m compileall -q kimico_gateway` → ok. No skipped/xfailed tests, no forbidden files, no runtime dependencies.
