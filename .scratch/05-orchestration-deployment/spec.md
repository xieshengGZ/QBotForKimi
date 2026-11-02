# 05 · Orchestration and Deployment

Status: ready-for-agent
Category: enhancement

## Problem Statement

The gateway’s modules are only useful when one supervised process starts them in the right order, keeps them alive, and shuts them down without touching kimico. The operator also needs a repeatable deployment: systemd must restart the gateway without killing tmux, the service user must be able to read its configuration, NapCat must be reachable on loopback, and failures must be diagnosable from the phone and the journal.

## Solution

Build the process orchestration and deployment layer: a main thread that loads configuration, preflights kimico, creates or takes over the tmux session, wires the modules, starts the HTTP, input, monitor, and sender threads, checks their health, and performs a graceful shutdown. Provide a systemd unit, a pinned NapCat Docker deployment, configuration ownership and environment setup, and the acceptance checks that prove the operational promises.

## User Stories

1. As an operator, I want one command to start the gateway, so that I do not need to launch modules manually.
2. As an operator, I want startup to preflight the kimico command, so that a missing binary fails immediately with a clear message instead of entering a recovery loop.
3. As an operator, I want startup to create or take over the tmux session before starting the threads, so that the first monitor round has a target.
4. As an operator, I want a port conflict or bind failure to exit with a distinct code, so that I can distinguish it from a bad configuration.
5. As an operator, I want systemd to restart the gateway after a crash or thread death, so that the phone control recovers automatically.
6. As an operator, I want systemd not to restart forever on a configuration error, so that journald is not flooded by a problem only a human can fix.
7. As an operator, I want `systemctl restart` to leave the tmux server and kimico PID unchanged, so that a gateway deploy does not destroy my kimico context.
8. As an operator, I want the service user to read the configuration file, so that startup does not fail on permissions.
9. As an operator, I want the service to have the correct PATH, HOME, and TERM, so that a kimico installed in a user environment is found and tmux behaves predictably.
10. As an operator, I want a warning when NapCat or the outbound API is unreachable, so that I can diagnose a one-way link.
11. As an operator, I want SIGTERM/SIGINT to stop the gateway cleanly, so that state is persisted and threads exit.
12. As an operator, I want shutdown to stop intake first, then join the input worker, then persist state, so that a pending command cannot race the final save.
13. As an operator, I want the gateway to check thread liveness every second, so that a silently dead monitor or sender does not leave a half-working service.
14. As an operator, I want a dead worker to cause a critical log and a non-zero exit, so that systemd can restart the whole gateway.
15. As an operator, I want `#status` to report tmux/kimico state, mirror, three queues, last output, and uptime, so that I can diagnose from the phone.
16. As an operator, I want a pinned NapCat image and loopback-only ports, so that deployments are reproducible and not exposed to the network.
17. As an operator, I want a documented NapCat login and OneBot configuration, so that I can connect the gateway QQ account correctly.
18. As an operator, I want risk, backup, upgrade, and uninstall notes, so that I can operate the gateway responsibly.
19. As a tester, I want a repeatable acceptance suite covering restart, permissions, history, recovery, queues, echo, security, and thread death, so that the system can be verified without relying on a demo.
20. As a tester, I want a real-kimico TUI gate before release, so that the polling/diff assumption is validated against the actual application.
21. As an implementer, I want a single monitor loop that owns the baseline, tracker, batch, and generation snapshot, so that there is one writer for output state.
22. As an implementer, I want the sender to drain control before output, so that status and alarms are not stuck behind bulk output.
23. As an implementer, I want startup errors to print to stderr and exit with the documented code, so that systemd and operators get the same signal.

## Implementation Decisions

- The main module owns startup ordering: parse arguments, load configuration, initialize logging, load state, preflight the kimico command, ensure the tmux session, construct components, bind the HTTP server, start threads, install signal handlers, and enter a health-check loop.
- Startup failure exits with: 2 for configuration, 3 for kimico preflight or tmux self-check, 4 for HTTP bind/port. Runtime thread death exits with 5. Normal shutdown exits 0.
- Threads are: single-threaded HTTP ingress, one input worker, one monitor, one sender. All are daemon threads but the main thread joins them during shutdown.
- The monitor loop is the only writer of the output baseline, stability tracker, batch buffer, and generation snapshot. It checks the reset event first, then pane state, then capture/diff/stability, then flushes the batch. It catches per-round exceptions so the thread survives a bad round.
- The monitor handles ALIVE with a liveness note, DEAD/MISSING with recovery, and UNKNOWN with a warning and no recovery. A successful respawn or recreate resets the baseline and batch.
- The status provider queries pane state with a bounded timeout and builds the eight-field snapshot. It never raises to the worker; failures become a degraded status reply.
- The sender drains the control lane first and waits different intervals for control and output. It checks generation before send and retry and catches per-segment exceptions.
- The main health loop checks every thread’s liveness every second. A dead thread logs CRITICAL, sets the stop event, and causes exit code 5 after cleanup.
- Shutdown order: set stop event, stop HTTP intake, join the input worker within five seconds, save mirror state, join the remaining threads within five seconds, do not touch tmux or kimico.
- The systemd unit uses `KillMode=process` so restart leaves the tmux server alive, `Restart=on-failure`, `RestartPreventExitStatus=2`, and start-limit settings. This is a documented transition; the long-term deployment should move tmux lifecycle to a separate unit or login session.
- The service runs as the configured user, with HOME, TERM, PATH, `no_proxy` for loopback, and an explicit Python entrypoint. The configuration file is installed owned by the service user with mode 600.
- NapCat runs in Docker with host networking, a pinned image version or digest, loopback-only API/report ports, array message format, and tokens matching the gateway. The deployment documents login, token configuration, and the difference between the main QQ and gateway QQ.
- The acceptance suite covers the ten base checks plus v1.1 supplements: restart PID stability, config readability, history capacity, startup-dead backoff, single failure count, concurrent clear/off, control-lane priority, echo edge cases, real kimico, concurrent ingress, security boundaries, tmux hang, thread death, and state corruption.

## Testing Decisions

A good test asserts observable process behavior: exit codes, thread liveness, persisted state, HTTP responses, injected text, sent messages, and PID stability. Tests must not assert private wiring details or depend on a real QQ account.

**Seam:** the highest seam is the full process with fake tmux and fake NapCat, plus a systemd-level restart check for the deployment promise. Unit tests for individual modules belong to their own specs; this spec tests orchestration.

- Process tests start the gateway with fake components, exercise startup, signal shutdown, thread death, and configuration/preflight/bind failures, and assert exit codes and logs.
- Restart tests record the tmux pane PID, run `systemctl restart`, and assert the PID is unchanged and the pane is alive. This is the deployment seam.
- Acceptance tests use the documented fake kimico, fake NapCat receiver, and event-posting helper; they cover the base and supplemental checks.
- The real-kimico gate runs the gateway against the actual TUI for 5–10 minutes and records overlap statistics, output loss, duplicate lines, and p95 latency. A failure stops the release.
- There is no existing test suite; the prior art is the product PRD’s acceptance checks and recovery test tables.

## Out of Scope

- New product features beyond the v1.1 PRD.
- Multi-session, multi-user, group chat, images, files, or voice.
- A web console, cloud scheduling, or cross-machine control.
- A full metrics endpoint; v1.1 uses structured logs and `#status` only.
- Replacing polling capture with `pipe-pane`; that is a future architectural change gated by the real-TUI spike.

## Further Notes

- This spec depends on Specs 01–04.
- `KillMode=process` is a deliberate transition. If the deployment owner can start tmux outside the gateway unit, switch to that layout and restore the default kill mode.
- The real-kimico gate is a release blocker, not a nice-to-have. Fake kimico proves the plumbing, not the TUI behavior.
- The acceptance checks are the source of truth for “done”; passing unit tests alone is not sufficient.
