# 11: Mirror pipeline, echo, and batching

**What to build:** Stable lines become readable QQ output only when mirror is on. The pipeline filters blank lines, suppresses input echo line by line, batches output during a quiet window, chunks at the configured limit, and tags every output item with the current generation so clear and mirror-off cannot leak old content.

**Blocked by:** 06 (Outbound bus and sender), 10 (Capture, overlap, and stability).

**Status:** done

- [x] With mirror off, stable lines are dropped and no output item is queued.
- [x] A stale generation is dropped before filtering or batching.
- [x] Ignore patterns remove matching lines before echo handling; an empty result produces no batch.
- [x] Echo suppression removes only matching lines, consumes the oldest registered echo once, and leaves the rest of the block intact.
- [x] Prompt-prefix matching works for the configured prefixes; an unmatched block consumes nothing.
- [x] Echo entries expire after ten seconds, are pruned on register and suppress, and are capped at 50.
- [x] A quiet-window expiry flushes one batch; a batch-size overflow flushes immediately.
- [x] Chunking preserves line order, keeps every chunk at or below the limit, and hard-splits only an overlong single line.
- [x] The documented echo false-positive trade-off is preserved and covered by a test.

## Comments

- W5-2 implementation complete.
- Commit: `d6d16b4c670abc195ad7a1b1a697f51d8608e95a` (`feat(mirror): pipeline, echo, and batching (11)`).
- Files: `kimico_gateway/mirror.py` (added `MirrorSwitch`, `OutputEpoch`, `normalize`, `EchoSuppressor`, `passes_filters`, `compile_patterns`, `chunk_lines`, `MirrorPipeline`; `OutboundBus` and `run_sender_loop` unchanged), `tests/test_mirror_pipeline.py` (37 new tests).
- Verification: `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` from `/home/ubuntu/qq/.worktrees/11` → `245 passed in 17.81s` (208 baseline + 37 new).
- Assumptions / remaining:
  - Batch-size overflow flushes directly inside `handle_stable_lines`; `flush_if_due` also checks the max as a safety net, so either call path flushes immediately.
  - `flush_if_due` additionally drops pending lines when the mirror switch is off or the epoch changed, matching immediate mirror-off semantics even if the caller did not bump the epoch.
  - Capture, monitor loop, input/onebot, and `__main__` wiring are intentionally out of scope (tickets 10/12).

### 控制器验收 · 2026-11-02

- 合并：`1da0a7e`（窗口提交 `d6d16b4`，rebase 后快进）。
- worktree：`pytest tests/ -q` → 245 passed（含 37 个 pipeline/echo/chunk 测试）；main 波次全量 271 passed；`compileall` 通过。
- 范围：`kimico_gateway/mirror.py`（仅新增 pipeline/echo/chunk 等，`OutboundBus`/`run_sender_loop` 未改）、`tests/test_mirror_pipeline.py`；未改 input/onebot/capture/router、PRD、全局文档；无 `.skip`/`xfail`、无运行时依赖。
- 关键点：mirror off 直接丢弃；stale generation 在过滤前丢弃；过滤 → 行级回显 → 批处理 → 切段 → 输出 lane；回显 10s/50、前缀匹配、只消费队首一次；quiet/max flush；chunk 整行优先、超长单行硬切；误删取舍有测试。
- 结论：9 条验收项全部通过，ticket 11 `done`。
