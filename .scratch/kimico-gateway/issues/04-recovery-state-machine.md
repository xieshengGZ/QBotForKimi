# 04: Recovery state machine

**What to build:** An operator can rely on the gateway to respawn a confirmed dead pane and recreate a missing tmux session with bounded backoff, without ever killing a live kimico process or counting one failure twice. Repeated failures stop recovery and send one alarm.

**Blocked by:** 03 (tmux session lifecycle and pane state).

**Status:** ready-for-agent

- [ ] A pane that dies after a long healthy run waits 5 seconds before the first respawn.
- [ ] A gateway that starts with a dead pane also waits 5 seconds for the first attempt.
- [ ] A process that dies within the success window records exactly one failure and moves to the next backoff step.
- [ ] A failed respawn command records exactly one failure and is not counted again on the next round.
- [ ] Reaching the configured failure maximum halts recovery and sends the alarm once.
- [ ] A MISSING session calls recreate, not respawn; an UNKNOWN state performs no recovery action.
- [ ] Recovery sleep can be interrupted by shutdown.
- [ ] Restart notifications and the alarm use the router copy templates with the correct wait, attempt, window, and maximum values.
