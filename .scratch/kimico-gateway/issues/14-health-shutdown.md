# 14: Health checks and graceful shutdown

**What to build:** A dead HTTP/input/monitor/sender thread is detected within one second and turns into a critical log, a stop signal, and exit code 5 so systemd can restart the gateway. SIGTERM/SIGINT stops intake first, joins the input worker, persists mirror state, joins the remaining threads, and never kills tmux or kimico.

**Blocked by:** 13 (Main process wiring and status provider).

**Status:** in-progress

- [ ] A thread that exits unexpectedly is detected within one second, logged at CRITICAL, and causes exit code 5 after cleanup.
- [ ] SIGTERM and SIGINT stop the HTTP server and input worker before the final state save.
- [ ] The final mirror state is written after the input worker has stopped.
- [ ] Thread joins time out after the configured grace period and log a warning without blocking shutdown forever.
- [ ] Shutdown performs no tmux session kill, pane kill, or kimico kill.
- [ ] A normal shutdown exits with code 0.
- [ ] Shutdown during a recovery sleep returns promptly.

## Comments

- W8-1 implementation complete.
- Commit: `0258a54a6a1b580471053502d1164565a88214d7` (`feat(main): health checks and graceful shutdown (14)`).
- Files: `kimico_gateway/__main__.py` (`Gateway.supervise()`, liveness loop, shutdown sequence, bounded joins; startup order/status provider/RuntimeState/run_monitor_loop preserved), `tests/test_health_shutdown.py` (new, 6 tests), `tests/test_main_wiring.py` (added `FakeThread.join` only; existing assertions unchanged).
- Verification (run in `/home/ubuntu/qq/.worktrees/14` with `/home/ubuntu/qq/.venv/bin/python`):
  - `-m pytest tests/test_health_shutdown.py tests/test_main_wiring.py tests/test_walking_skeleton.py tests/test_monitor_loop.py -q` → 38 passed in 4.13s.
  - `-m pytest -q` → 370 passed in 21.49s.
  - `-m compileall -q kimico_gateway` → ok.
- Implementation notes:
  - `Gateway.supervise()` checks every worker immediately and then every `HEALTH_CHECK_SECONDS = 1.0`. A dead worker logs CRITICAL `thread died: <name>`, sets `stop_event`, runs cleanup, and returns exit code 5. `Gateway.wait()` remains an alias for `supervise()`.
  - Shutdown order is: set stop event → `server.shutdown()` → join input worker (up to `SHUTDOWN_JOIN_SECONDS = 5.0`) → final `state_store.save(switch.is_on())` → join http/monitor/sender (5s each). A join timeout logs WARNING and continues; exceptions from HTTP shutdown, final save, or join are logged and do not stop the remaining cleanup.
  - No tmux/kimico seam is called during shutdown. Recovery sleep uses `stop_event.wait`, so a stop request interrupts a pending backoff promptly.
- Unfinished / assumptions:
  - Ticket 15 (systemd/NapCat deployment) and ticket 16 (acceptance) are out of scope.
  - The five-second grace period is a module constant; no configuration key or `config.py` change was introduced.
  - Thread-death handling is tested with controllable fake threads; SIGTERM/SIGINT ordering is tested through the signal-handler seam, and the real-subprocess walking-skeleton SIGTERM/SIGINT tests still exit 0 with empty stderr.
  - Ticket Status and acceptance checkboxes were left unchanged for the controller.
