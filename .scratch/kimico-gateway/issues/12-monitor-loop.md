# 12: Monitor loop integration

**What to build:** One monitor thread owns the output baseline, stability tracker, batch, and generation snapshot. Each round it checks reset requests, pane state, recovery, capture, overlap, stability, and batching, and it keeps running after a single-round failure.

**Blocked by:** 04 (Recovery state machine), 11 (Mirror pipeline, echo, and batching).

**Status:** in-progress

- [ ] A reset request clears the batch and tracker, resets the baseline, and produces no output in that round.
- [ ] ALIVE records liveness; DEAD/MISSING invokes recovery; UNKNOWN logs a warning and is not treated as dead.
- [ ] A successful respawn or recreate resets the baseline, tracker, and batch.
- [ ] Capture, overlap, stability, and batch flush are wired in the documented order.
- [ ] `k=0` first attempts inserted-line recovery; only the configured consecutive threshold rebuilds the baseline.
- [ ] A released stable block updates last-output time regardless of mirror state and is passed to the pipeline with the current generation.
- [ ] The batch is flushed on quiet-window expiry even when no new lines were released.
- [ ] A single-round exception is logged and the monitor continues to the next round.
