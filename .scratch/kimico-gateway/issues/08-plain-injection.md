# 08: Plain-text injection worker

**What to build:** Ordinary QQ text is consumed from the inbound queue in order and injected into kimico unchanged, including multi-line text, without overwriting the operator’s own tmux paste buffer. `##` sends a literal `#`, whitespace-only messages are ignored, and injection failures return the fixed failure reply.

**Blocked by:** 07 (OneBot ingress and inbound queue).

**Status:** done

- [x] The worker consumes the inbound queue FIFO and injects consecutive messages in arrival order.
- [x] Ordinary text is injected unchanged, including quotes, newlines, and leading/trailing spaces.
- [x] A multi-line message is pasted as one bracketed paste with one Enter, and linefeeds are preserved.
- [x] Injection uses a unique named buffer, deletes it after paste or on failure, and does not overwrite the default tmux buffer.
- [x] A message beginning with `##` injects the text after the first `#`; leading whitespace prevents the escape.
- [x] Empty and whitespace-only messages cause no injection and no reply.
- [x] NUL characters are rejected before invoking tmux.
- [x] A tmux timeout or failure returns the fixed injection-failure reply and logs without the message text.
- [x] Successful ordinary injection is silent, with no ACK.

## Comments

- 2026-11-02 W5-1: implemented `InjectError`, `inject`, and `run_input_worker` in `kimico_gateway/input.py`; added `tests/test_plain_injection.py` (26 tests covering tmux argv/timeout/NUL/buffer cleanup, FIFO order, `##`, empty input, the control-handler seam, failure logging without text, exception isolation, and prompt stop).
- Commit: `eb5b3f681e651813112c2494113236780ebf91c2` (`feat(input): plain-text injection worker (08)`).
- Commands/results: `/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_plain_injection.py -q` → 26 passed; `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` → 234 passed.
- Real tmux sanity check under an isolated `TMUX_TMPDIR`: multiline text pasted in one operation, the default tmux buffer preserved, and the named buffer deleted automatically.
- Assumptions/leftovers: control-command semantics are not implemented. `run_input_worker` exposes an optional `control_handler(text) -> bool` seam for ticket 09; without it, control-looking text (`#` after optional leading whitespace, except column-zero `##`) is logged at DEBUG and dropped. `mirror_switch`, `epoch`, `reset_baseline`, and `status_provider` are accepted but unused until ticket 09. On success the named buffer is removed by `paste-buffer -d`; an explicit `delete-buffer` runs only on failure.

### 控制器验收 · 2026-11-02

- 合并：`eb5b3f6`（`git merge --ff-only feat/08-plain-injection`）。
- worktree：`pytest tests/ -q` → 234 passed（含 26 个注入测试）；main 波次全量 271 passed；`compileall` 通过。
- 范围：`kimico_gateway/input.py`、`tests/test_plain_injection.py`；未改 router/mirror/onebot/capture、PRD、全局文档；无 `.skip`/`xfail`、无运行时依赖。
- 关键点：唯一命名 buffer + `paste-buffer -d -p -r` + 单次 `C-m`；`=<session>:` 目标；NUL 拒绝；失败清理且错误不含正文；`##` 列首转义；控制类文本在未安装 handler 时丢弃、不注入；成功 `echo.register`、无 ACK。
- 结论：9 条验收项全部通过，ticket 08 `done`。控制指令语义留给 09，worker 已提供 `control_handler` seam。
