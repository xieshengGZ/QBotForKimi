# 14: Health checks and graceful shutdown

**What to build:** A dead HTTP/input/monitor/sender thread is detected within one second and turns into a critical log, a stop signal, and exit code 5 so systemd can restart the gateway. SIGTERM/SIGINT stops intake first, joins the input worker, persists mirror state, joins the remaining threads, and never kills tmux or kimico.

**Blocked by:** 13 (Main process wiring and status provider).

**Status:** ready-for-agent

- [ ] A thread that exits unexpectedly is detected within one second, logged at CRITICAL, and causes exit code 5 after cleanup.
- [ ] SIGTERM and SIGINT stop the HTTP server and input worker before the final state save.
- [ ] The final mirror state is written after the input worker has stopped.
- [ ] Thread joins time out after the configured grace period and log a warning without blocking shutdown forever.
- [ ] Shutdown performs no tmux session kill, pane kill, or kimico kill.
- [ ] A normal shutdown exits with code 0.
- [ ] Shutdown during a recovery sleep returns promptly.
