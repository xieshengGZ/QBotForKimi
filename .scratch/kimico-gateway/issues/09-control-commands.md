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
