# 10: Capture, overlap, and stability

**What to build:** The gateway can turn repeated tmux pane snapshots into a stream of stable new lines. It finds the previous/current overlap in linear time, tolerates a volatile bottom region, recovers inserted lines after a failed overlap, and delays the last non-empty line one extra round.

**Blocked by:** 02 (Complete configuration contract).

**Status:** done

- [x] Capture uses a joined, history-aware tmux command with a timeout; a non-zero exit or timeout surfaces as a capture error.
- [x] The overlap algorithm matches a brute-force definition on random, duplicate-heavy, append, scroll, rewrite, and empty inputs.
- [x] A 5000-line duplicate-heavy input completes within the agreed linear-time budget.
- [x] Masking the configured tail lines lets an otherwise volatile screen still produce a valid overlap.
- [x] A zero-overlap snapshot first recovers pure insert blocks; only after the configured consecutive threshold is the baseline rebuilt.
- [x] Stability releases only a prefix, delays the last non-empty line one extra round, and resets a changed line and everything after it.
- [x] Empty feeds advance candidate counters, and reset clears the candidate block.
- [x] Baseline initialization produces no output.

## Comments

- 2026-11-02 W4-3:
  - Commit: `d07647ea7d0edbc6a0925c81ff48f978b7310c3f` on `feat/10-capture-stability` (baseline `1149b75`).
  - Files: `kimico_gateway/capture.py` (new), `tests/test_capture.py` (new). No other module, PRD, ADR, existing test, or global document changed.
  - Command: `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` from `/home/ubuntu/qq/.worktrees/10`.
  - Result: `160 passed` (37 new capture tests + 123 existing); no skips, no `xfail`, no third-party runtime dependency. `compileall` passes.
  - Acceptance:
    - `capture_pane`: `tmux capture-pane -p -J -S -<lines> -t =<session>:` (list form, no shell), `timeout` keyword, `splitlines()`; empty output -> `[]`, internal blank lines preserved; non-zero exit / `TimeoutExpired` / `OSError` -> `CaptureError` (stderr truncated to 200 chars, no body). Unit tests assert the exact argv and one isolated real-tmux test proves `=<session>:` works.
    - `find_overlap` (KMP) is compared with the literal brute-force definition on 900 random/duplicate-heavy cases plus append/scroll/rewrite/empty cases. 5000-line duplicate-heavy inputs complete in ~1.6 ms (test budget 50 ms).
    - `mask_tail` replaces only the tail; a screen whose bottom line churns yields `k=0` raw and `k>0` after masking.
    - `recover_new_lines` returns only `insert` opcodes; `LiveTailTracker.update` tries recovery first on `k=0`, advances the baseline on success, and rebuilds only after `k0_rebuild_threshold` consecutive failures.
    - `LiveTailTracker` main path is `cp = common_prefix(prev, cur)` + live-tail replacement via `sync(cur, cp)`. Prefix-only release, the extra round for the last non-empty line, changed-line reset, empty-round counter advancement, and `reset()` are covered. Live-tail lines are never released while still in the tail.
    - Baseline (`update` after construction/`reset`) returns no output; pure append and half-line completion (`ANS-000 half` -> `ANS-000 complete`) are covered. A 65-line streaming answer with churning status/fence lines is delivered exactly once, in order.
  - Assumptions / not done:
    - `LiveTailTracker` API: `update(cur)`, `sync(cur, cp)`, `shift(delta)`, `reset()`, read-only `has_baseline` / `baseline` / `k0_streak`. No separate `StabilityTracker` class was added; ADR-0001 replaces PRD §6's block feed API.
    - `min_common_prefix` defaults to 5 (the ADR/spike heuristic) and is constructor-overridable; it keeps a drastic rewrite from being mistaken for new live-tail output. Real screens have a much larger `cp`, so this does not affect the 300 ms path.
    - Recovered insert blocks are returned immediately (last-resort path) and the baseline advances; they do not go through an additional stability delay. This preserves the 60+ streaming acceptance and avoids losing recovered lines.
    - `find_overlap` is still computed each `update` round (useful for ticket-12 metrics); it is O(n), ~1.6 ms at 5000 duplicate-heavy lines.
    - No monitor loop (ticket 12), mirror pipeline (ticket 11), `__main__`, or systemd wiring implemented. The real-tmux test uses an isolated `TMUX_TMPDIR`, `flock /tmp/kimico-gateway-integration.lock`, and kills only its own server.

### 控制器验收 · 2026-11-02

- 合并：`a80e272`（窗口提交 `d07647e`，rebase 后快进）。
- worktree：`pytest tests/ -q` → 160 passed（含 37 个 capture 测试）；main 波次全量 208 passed；`compileall` 通过。
- 独立复核：用 `docs/output/spike/frames/frames.jsonl` 的 77 帧真实 TUI 数据回放产品 `LiveTailTracker` → **60/60 标签送达、0 重复、p95 0.608s**；5000 行重复输入 `find_overlap` 约 1.6ms。
- 范围：`kimico_gateway/capture.py`、`tests/test_capture.py`；未改 PRD/ADR、monitor/mirror/`__main__`；无运行时依赖。
- 实现：`capture_pane` 使用 `=<session>:`；主路径稳定前缀 + 活尾同步；`find_overlap`/`mask_tail`/`recover_new_lines` 保留为回退；半行完成替换、基线无输出、前缀释放、尾行 +1、窗口滑动、k=0 阈值重建均有测试；真实 tmux 测试使用 `TMUX_TMPDIR` 隔离 + flock。
- 结论：8 条验收项 + ADR-0001 全部通过，ticket 10 `done`。
