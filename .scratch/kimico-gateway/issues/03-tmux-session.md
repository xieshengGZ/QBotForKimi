# 03: tmux session lifecycle and pane state

**What to build:** An operator can let the gateway create a tmux session on first run or take over an existing one. The session has the configured history capacity and remain-on-exit behavior, and the gateway can report ALIVE, DEAD, MISSING, or UNKNOWN without confusing a transient tmux failure for a dead kimico process.

**Blocked by:** 02 (Complete configuration contract).

**Status:** ready-for-agent

- [ ] Creating a session sets the history limit before the pane is created, then enables and verifies remain-on-exit.
- [ ] Taking over an existing session enables and verifies remain-on-exit without creating a new session.
- [ ] Taking over a session whose pane history is below the configured limit produces a warning rather than silently claiming success.
- [ ] A session with an unexpected number of windows or panes fails startup with a clear error.
- [ ] Pane state maps running, exited, missing, and unparseable/timeout results to ALIVE, DEAD, MISSING, and UNKNOWN respectively.
- [ ] Every tmux command has a timeout and normalizes failures into a tmux error.
- [ ] The public session API can be exercised against a real tmux server in a test harness without requiring the rest of the gateway.
