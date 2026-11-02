# 11: Mirror pipeline, echo, and batching

**What to build:** Stable lines become readable QQ output only when mirror is on. The pipeline filters blank lines, suppresses input echo line by line, batches output during a quiet window, chunks at the configured limit, and tags every output item with the current generation so clear and mirror-off cannot leak old content.

**Blocked by:** 06 (Outbound bus and sender), 10 (Capture, overlap, and stability).

**Status:** ready-for-agent

- [ ] With mirror off, stable lines are dropped and no output item is queued.
- [ ] A stale generation is dropped before filtering or batching.
- [ ] Ignore patterns remove matching lines before echo handling; an empty result produces no batch.
- [ ] Echo suppression removes only matching lines, consumes the oldest registered echo once, and leaves the rest of the block intact.
- [ ] Prompt-prefix matching works for the configured prefixes; an unmatched block consumes nothing.
- [ ] Echo entries expire after ten seconds, are pruned on register and suppress, and are capped at 50.
- [ ] A quiet-window expiry flushes one batch; a batch-size overflow flushes immediately.
- [ ] Chunking preserves line order, keeps every chunk at or below the limit, and hard-splits only an overlong single line.
- [ ] The documented echo false-positive trade-off is preserved and covered by a test.
