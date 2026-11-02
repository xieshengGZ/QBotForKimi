# 16: Acceptance suite and real-kimico gate

**What to build:** A repeatable acceptance suite proves the operational promises without real QQ, and a real-kimico gate proves that the TUI capture/echo assumptions hold. Passing unit tests alone is not enough; the suite covers restart, permissions, history, recovery, queues, echo, security, tmux hang, thread death, and state corruption.

**Blocked by:** 15 (systemd and NapCat deployment).

**Status:** in-progress

- [ ] The fake-kimico and fake-NapCat acceptance environment runs the ten base checks and the v1.1 supplemental checks.
- [ ] `systemctl restart` leaves the tmux/kimico PID unchanged.
- [ ] The service user can read the configuration file.
- [ ] A 3000-line output run captures more than 2000 lines.
- [ ] Startup with a dead pane uses a 5-second first backoff; a failed respawn is counted once.
- [ ] Concurrent `#clear` and `#mirror off` produce no stale-generation output.
- [ ] A flooded output lane still delivers `#status` and alarms within the control-lane target.
- [ ] Echo edge cases, concurrent ingress order, security boundaries, tmux hang, thread death, and state corruption are covered.
- [ ] A 5–10 minute real-kimico run records overlap statistics, output loss, duplicates, and p95 latency; no answer lines are lost and p95 is within target.

## Comments

- 2026-11-02 W10-1：实现 `acceptance/` 可重复套件（fake kimico/NapCat、隔离 `TMUX_TMPDIR`、随机回环端口、fake 环境 runner、systemd 检查、真实 kimi 门禁），报告见 `docs/output/acceptance-report.md`。
- 假环境：`run_acceptance.py` 结果 **20 PASS / 0 FAIL / 3 SKIP**；SKIP 为验收 9/11（独立 `systemd_check.py`，PASS）与验收 18（独立 `real_kimico_gate.py`，PASS）。
- systemd：`systemctl restart` 前后 tmux server PID 2301971 与 pane PID 479886 均不变，`pane_dead=0`；config `600 ubuntu:ubuntu` 且 `sudo -u ubuntu test -r` 成功；检查后服务已恢复 inactive/disabled。
- 真实 kimi（338.33s）：39/39 标签送达，0 丢失，0 重复回答行，p95 1.662s，max 1.662s；`capture_k0_total` 43/1082（3.97%），`recover_new_lines` 7/7 成功，capture 错误 0。使用 3 个 12 行长回答 + 3 个短回答，本地/手机交替。
- 验收暴露的真实缺陷已修复：OneBot HTTP listen backlog 5→64（20 并发连接不再 reset）；默认 `echo_prefix_patterns` 增加真实 kimi 使用的 `❯`。两处均补了回归测试。
- `pytest -q`：**403 passed**。环境偏离：端口 3000 被 `shotsmith-web-1` 占用，假 NapCat 使用随机回环端口；未触碰 `shotsmith-*` 与用户 tmux 会话 `kimi02`/`qq-6`/`test`。
