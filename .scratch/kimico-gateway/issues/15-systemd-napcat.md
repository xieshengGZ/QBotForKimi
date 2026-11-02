# 15: systemd and NapCat deployment

**What to build:** An operator can install the gateway as a systemd service that restarts on failure but not on configuration errors, leaves tmux and kimico alive across a gateway restart, reads its configuration as the service user, finds kimico through the service environment, and reaches a pinned NapCat container on loopback.

**Blocked by:** 14 (Health checks and graceful shutdown).

**Status:** in-progress

- [ ] The systemd unit uses `KillMode=process`, `Restart=on-failure`, a prevented restart on exit code 2, and start-limit settings.
- [ ] `systemctl restart` leaves the tmux/kimico pane PID unchanged.
- [ ] The configuration file is owned by the service user with mode 600 and is readable by that user.
- [ ] The service has HOME, TERM, PATH, and loopback `no_proxy` set, and an absolute kimico command or an effective PATH.
- [ ] The NapCat container uses a pinned image version or digest, host networking, and loopback-only API/report ports.
- [ ] OneBot is configured for array message format, matching tokens, and the documented report URL.
- [ ] The deployment guide documents the main QQ versus gateway QQ distinction, risk, backup, upgrade, and uninstall.
