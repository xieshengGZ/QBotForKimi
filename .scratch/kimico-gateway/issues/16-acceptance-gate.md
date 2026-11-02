# 16: Acceptance suite and real-kimico gate

**What to build:** A repeatable acceptance suite proves the operational promises without real QQ, and a real-kimico gate proves that the TUI capture/echo assumptions hold. Passing unit tests alone is not enough; the suite covers restart, permissions, history, recovery, queues, echo, security, tmux hang, thread death, and state corruption.

**Blocked by:** 15 (systemd and NapCat deployment).

**Status:** ready-for-agent

- [ ] The fake-kimico and fake-NapCat acceptance environment runs the ten base checks and the v1.1 supplemental checks.
- [ ] `systemctl restart` leaves the tmux/kimico PID unchanged.
- [ ] The service user can read the configuration file.
- [ ] A 3000-line output run captures more than 2000 lines.
- [ ] Startup with a dead pane uses a 5-second first backoff; a failed respawn is counted once.
- [ ] Concurrent `#clear` and `#mirror off` produce no stale-generation output.
- [ ] A flooded output lane still delivers `#status` and alarms within the control-lane target.
- [ ] Echo edge cases, concurrent ingress order, security boundaries, tmux hang, thread death, and state corruption are covered.
- [ ] A 5–10 minute real-kimico run records overlap statistics, output loss, duplicates, and p95 latency; no answer lines are lost and p95 is within target.
