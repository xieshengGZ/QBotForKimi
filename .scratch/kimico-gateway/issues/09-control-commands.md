# 09: Control commands and status

**What to build:** The operator can send `#mirror on`, `#mirror off`, `#status`, and `#clear` from the phone and receive the exact documented replies. Unknown `#` commands return usage instead of being injected, control replies always enter the control lane, and clear/mirror-off invalidate old output by generation.

**Blocked by:** 06 (Outbound bus and sender), 08 (Plain-text injection worker).

**Status:** in-progress

- [ ] `#mirror on`, `#mirror off`, `#status`, and `#clear` are recognized case-insensitively with arbitrary surrounding whitespace.
- [ ] Unknown commands, missing arguments, extra arguments, and a bare `#` return the usage text and are not injected.
- [ ] `#mirror off` sets the switch, increments the output generation, clears only the output lane, then queues the reply; the reply is not cleared.
- [ ] `#clear` increments the generation, clears only the output lane, requests a baseline reset, then queues the reply.
- [ ] `#status` returns the eight documented lines with the correct tmux, kimico, mirror, inbound, control, output, last-output, and uptime values.
- [ ] A status-provider failure returns the degraded status reply instead of crashing the worker.
- [ ] All control replies are placed in the control lane and are not affected by mirror state.
- [ ] Copy strings match the router table byte-for-byte.

## Comments

- 2026-11-02 W6-1: implemented control commands and `#status` in `kimico_gateway/router.py` (`CommandKind`, `parse_command`, `StatusSnapshot`, `format_status`, fixed copy table) and wired internal dispatch plus ordered side effects into `kimico_gateway/input.py`; added `tests/test_control_commands.py` (67 tests: parser table, byte-for-byte copy, eight-line status template/uptime/last-output, mirror on/off and clear ordering with a real `OutputEpoch`, status degradation, control-lane isolation, case/whitespace variants, unknown-command usage, `##`/full-width passthrough, and handler-override compatibility).
- Commit: `c85320cc2f5444e69fc1aa5a68aa1b2ad7e7693b` (`feat(input): control commands and status (09)`).
- Commands/results: `/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_control_commands.py tests/test_plain_injection.py -q` → 93 passed; `/home/ubuntu/qq/.venv/bin/python -m pytest -q` → 338 passed (271 baseline + 67 new).
- Assumptions/leftovers:
  - `control_handler` (ticket 08 seam) still takes precedence when supplied; the default path dispatches internally through `parse_command`.
  - To keep ticket 08's "no handler, controls not wired" tests meaningful, the worker dispatches controls only when `mirror_switch.set`, `epoch.bump`, and `reset_baseline.set` are all callable; otherwise control-looking text is logged at DEBUG and dropped.
  - `format_status` formats the last-output timestamp directly with `time.localtime`; the `now_wall` argument is accepted per the documented signature but unused.
  - `#status` snapshot assembly (tmux/kimico probes, queue lengths, start time) stays with the caller; `__main__` wiring is out of scope for this ticket.
