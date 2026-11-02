# 13: Main process wiring and status provider

**What to build:** One process starts the gateway in the documented order: load configuration, preflight the kimico command, create or take over the tmux session, construct the components, bind the HTTP server, start the HTTP/input/monitor/sender threads, and provide the eight-line status snapshot. Configuration, preflight/tmux, and bind failures exit with the correct codes.

**Blocked by:** 05 (Mirror state persistence), 07 (OneBot ingress and inbound queue), 09 (Control commands and status), 11 (Mirror pipeline, echo, and batching), 12 (Monitor loop integration).

**Status:** in-progress

- [ ] A valid startup loads configuration and state, preflights kimico, ensures the tmux session, binds the HTTP server, and starts all four threads.
- [ ] A missing kimico command exits with code 3 and a clear stderr message before starting threads.
- [ ] A tmux self-check failure exits with code 3.
- [ ] An HTTP bind/port failure exits with code 4.
- [ ] A configuration error exits with code 2.
- [ ] The status provider returns the eight documented fields and a bounded pane-state query.
- [ ] `#status` works through the real wiring with fake tmux and fake outbound transport.
- [ ] Runtime configuration edits do not alter the running process.
