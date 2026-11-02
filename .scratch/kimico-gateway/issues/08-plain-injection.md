# 08: Plain-text injection worker

**What to build:** Ordinary QQ text is consumed from the inbound queue in order and injected into kimico unchanged, including multi-line text, without overwriting the operator’s own tmux paste buffer. `##` sends a literal `#`, whitespace-only messages are ignored, and injection failures return the fixed failure reply.

**Blocked by:** 07 (OneBot ingress and inbound queue).

**Status:** ready-for-agent

- [ ] The worker consumes the inbound queue FIFO and injects consecutive messages in arrival order.
- [ ] Ordinary text is injected unchanged, including quotes, newlines, and leading/trailing spaces.
- [ ] A multi-line message is pasted as one bracketed paste with one Enter, and linefeeds are preserved.
- [ ] Injection uses a unique named buffer, deletes it after paste or on failure, and does not overwrite the default tmux buffer.
- [ ] A message beginning with `##` injects the text after the first `#`; leading whitespace prevents the escape.
- [ ] Empty and whitespace-only messages cause no injection and no reply.
- [ ] NUL characters are rejected before invoking tmux.
- [ ] A tmux timeout or failure returns the fixed injection-failure reply and logs without the message text.
- [ ] Successful ordinary injection is silent, with no ACK.
