# 02 · tmux Session and Recovery

Status: ready-for-agent
Category: enhancement

## Problem Statement

The gateway must control a kimico process that lives inside a tmux session. It has to create that session on first run, take over an existing one, distinguish a dead pane from a transient tmux failure, and recover kimico without killing a live process. A false “dead” reading would destroy the user’s kimico context; a missing recovery path would leave the phone controlling nothing.

## Solution

Provide an idempotent tmux session lifecycle and a conservative recovery state machine. Session creation sets the history limit before the pane exists and enables remain-on-exit. Pane detection has four states: ALIVE, DEAD, MISSING, and UNKNOWN. Recovery respawns only a confirmed dead pane, recreates only a missing session, and never acts on UNKNOWN. Failures are counted once per attempt, with backoff, a sliding window, and a one-time alarm.

## User Stories

1. As an operator, I want the gateway to create the tmux session when it does not exist, so that first startup works without manual tmux commands.
2. As an operator, I want the gateway to take over an already-running tmux session, so that I can start kimico myself before installing the gateway.
3. As an operator, I want takeover to enable remain-on-exit idempotently, so that recovery works even when the session was created manually.
4. As an operator, I want the history limit set before the pane is created, so that long kimico output is actually retained.
5. As an operator, I want a warning when an existing pane has a lower history limit than configured, so that I know long output may be truncated.
6. As an operator, I want the gateway to verify the session has exactly one window and one pane, so that it does not operate on an unexpected layout.
7. As an operator, I want a live pane never to be killed because a tmux command timed out or failed, so that a transient tmux problem cannot destroy kimico’s context.
8. As an operator, I want a confirmed dead pane to be respawned with 5s/15s/60s backoff, so that a crash recovers automatically without hammering the machine.
9. As an operator, I want a missing tmux session to be recreated with the same backoff, so that a tmux server restart does not leave the gateway permanently dead.
10. As an operator, I want the first observation of a dead pane to wait 5s, not 15s, so that the backoff matches the documented first attempt.
11. As an operator, I want each failed recovery attempt counted once, so that a single failure cannot skip a backoff step or trigger an early alarm.
12. As an operator, I want recovery failures measured in a sliding window, so that occasional failures spread over time do not permanently halt recovery.
13. As an operator, I want a QQ alarm after the configured failure maximum, so that I know automatic recovery has stopped.
14. As an operator, I want the alarm sent only once, so that I am not spammed after the gateway has already given up.
15. As an operator, I want recovery notifications before each attempt, so that I can see what the gateway is doing.
16. As an operator, I want recovery to be interruptible by shutdown, so that SIGTERM does not wait for a 60-second sleep.
17. As an implementer, I want a four-state pane result instead of a boolean, so that UNKNOWN cannot be mistaken for DEAD.
18. As an implementer, I want every tmux subprocess call to have a timeout, so that a wedged tmux server cannot hang the monitor or input thread.
19. As an implementer, I want recovery to accept injected clock, sleep, respawn, recreate, and pane-state callbacks, so that tests can drive the state machine without real tmux.
20. As an implementer, I want a clear distinction between respawn and recreate in logs, so that operators can tell whether kimico’s context was lost.

## Implementation Decisions

- The session module exposes an idempotent ensure-session operation, a four-state pane query, a respawn operation, and a recovery manager. The recovery manager is used by the monitor thread only.
- Pane states are: ALIVE when the pane process is running; DEAD when the pane exists and the process has exited; MISSING when the tmux session or server does not exist; UNKNOWN when a tmux command fails, times out, or returns an unparseable value.
- Only DEAD triggers respawn. Only MISSING triggers session recreation. UNKNOWN logs a warning, skips the round, and never calls respawn.
- Session creation order is fixed: set the global history limit, create the session with the kimico command, enable remain-on-exit for the window, then verify both options. Taking over an existing session sets remain-on-exit idempotently and verifies it, but does not attempt to resize an existing pane’s history.
- The kimico command is split into argv at startup and the first argv element is preflighted by the orchestration layer. Shell operators are not supported.
- Every tmux call uses an argument list, no shell, and a five-second timeout. Timeouts and non-zero exits normalize to a tmux error.
- Recovery state consists of failure timestamps, the start of the current alive period, a pending-attempt flag, a halted flag, and an alarmed flag.
- A pending attempt is settled exactly once: it clears failures if the process lived long enough, otherwise it records one failure. A first observation that is already dead does not record a failure. A failed respawn records one failure immediately and does not get counted again on the next round.
- Backoff is indexed by the number of current failures, clamped to the last entry. The attempt number is failures plus one. The sliding window removes old failure timestamps before the halt check.
- When the failure count reaches the maximum, the manager halts, sends one alarm, and stops respawning until the gateway process restarts.
- Notifications are rendered from the router copy templates and sent through the control lane. The alarm includes the window and maximum.
- Recovery sleeps through an interruptible wait so shutdown is not delayed.

## Testing Decisions

A good test drives the public session and recovery APIs and asserts observable state transitions, command effects, notifications, and return values. Tests must not assert private counters directly or depend on real tmux. Command construction may be asserted because the tmux command is the external contract.

**Seam:** the session public API plus the recovery manager with injected clock, sleep, respawn, recreate, and pane-state callbacks. One fake tmux adapter can serve all session tests; one scenario-driven clock serves the recovery tests.

- Session tests cover: create path sets history limit before session creation; takeover sets remain-on-exit and does not create a new session; low history produces a warning; command failures raise a tmux error; pane parsing maps 0/1/non-zero/timeouts to ALIVE/DEAD/UNKNOWN; MISSING is produced only when the session query says it does not exist.
- Recovery tests cover: first death after a long life; first observation already dead; death 30 seconds after respawn; three early deaths leading to halt; respawn failure counted once; missing session calls recreate; unknown state is ignored by the caller; external revival during sleep; window sliding; alarm once; notification text.
- A fake clock advances time explicitly; a fake sleep records waits and can be interrupted. Tests should use a scenario table rather than duplicating long setup code.
- There is no existing test suite; the prior art is the product PRD’s recovery test table.

## Out of Scope

- Output capture, diffing, stability, or mirroring.
- QQ ingress, command parsing, or message delivery.
- Main-thread orchestration, health checks, or deployment.
- Rebuilding a tmux server that disappears for reasons other than missing session detection; that is the orchestration/deployment seam.

## Further Notes

- This spec depends on the configuration contract from Spec 01.
- `KillMode=process` in deployment is what keeps a gateway restart from killing the tmux server; that belongs to Spec 05.
- Recovery recreates a missing session even though kimico’s in-memory context is already lost. The alarm must state that context may be lost.
