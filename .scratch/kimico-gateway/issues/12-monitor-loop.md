# 12: Monitor loop integration

**What to build:** One monitor thread owns the output baseline, stability tracker, batch, and generation snapshot. Each round it checks reset requests, pane state, recovery, capture, overlap, stability, and batching, and it keeps running after a single-round failure.

**Blocked by:** 04 (Recovery state machine), 11 (Mirror pipeline, echo, and batching).

**Status:** done

- [x] A reset request clears the batch and tracker, resets the baseline, and produces no output in that round.
- [x] ALIVE records liveness; DEAD/MISSING invokes recovery; UNKNOWN logs a warning and is not treated as dead.
- [x] A successful respawn or recreate resets the baseline, tracker, and batch.
- [x] Capture, overlap, stability, and batch flush are wired in the documented order.
- [x] `k=0` first attempts inserted-line recovery; only the configured consecutive threshold rebuilds the baseline.
- [x] A released stable block updates last-output time regardless of mirror state and is passed to the pipeline with the current generation.
- [x] The batch is flushed on quiet-window expiry even when no new lines were released.
- [x] A single-round exception is logged and the monitor continues to the next round.

## Comments

- W6-2 implementation complete.
- Commit: `caca0d21b6ac584e7ecb66d09841974f44a2adfa` (`feat(main): monitor loop integration (12)`).
- Files: `kimico_gateway/__main__.py` (added `RuntimeState` and `run_monitor_loop`; `main()` unchanged), `tests/test_monitor_loop.py` (12 new tests).
- Verification (run in `/home/ubuntu/qq/.worktrees/12`):
  - `/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_monitor_loop.py -q` → 12 passed in 0.06s.
  - `/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_monitor_loop.py tests/test_walking_skeleton.py -q` → 18 passed in 1.14s.
  - `/home/ubuntu/qq/.venv/bin/python -m pytest -q` → 283 passed in 17.02s.
- Implementation notes:
  - `run_monitor_loop` uses `LiveTailTracker` directly for baseline, redraw-aware diff, KMP overlap fallback, insert recovery, consecutive k=0 rebuild, stability, and tail delay, so `capture.py` is untouched.
  - Round order is reset request → pane state/recovery → capture/tracker update → released lines update last-output and enter the pipeline with `epoch.current()` → `flush_if_due()`. `flush_if_due()` runs once every round, including early DEAD/MISSING/UNKNOWN rounds and after a logged exception.
  - DEAD/MISSING skip capture for that round (per PRD §5); on successful recovery the tracker, pipeline batch, and baseline are reset. UNKNOWN only logs a warning and never calls recovery.
  - Reset round clears the pipeline batch and tracker, then continues to capture so the next snapshot becomes the new baseline; nothing is emitted for the pre-reset state.
- Unfinished / assumptions:
  - `main()` still only loads config, installs signal handlers, and waits; assembling components and starting/joining the four threads remains the orchestration ticket, per this ticket's file scope.
  - A `CaptureError` is logged at WARNING and other round exceptions at ERROR with traceback; both skip the rest of the round but keep the loop alive.

- 2026-11-02 W6 controller: rebased onto main (after 09), verified the branch's own `kimico_gateway/` and `tests/` trees are unchanged from the tested commit, merged `ee350d5` (feature) to main with `git merge --ff-only`. Wave-end on main: `/home/ubuntu/qq/.venv/bin/python -m pytest -q` → 350 passed; `/home/ubuntu/qq/.venv/bin/python -m compileall -q kimico_gateway` → ok. No skipped/xfailed tests, no forbidden files, no runtime dependencies.
