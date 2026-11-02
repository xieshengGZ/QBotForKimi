# 10: Capture, overlap, and stability

**What to build:** The gateway can turn repeated tmux pane snapshots into a stream of stable new lines. It finds the previous/current overlap in linear time, tolerates a volatile bottom region, recovers inserted lines after a failed overlap, and delays the last non-empty line one extra round.

**Blocked by:** 02 (Complete configuration contract).

**Status:** in-progress

- [ ] Capture uses a joined, history-aware tmux command with a timeout; a non-zero exit or timeout surfaces as a capture error.
- [ ] The overlap algorithm matches a brute-force definition on random, duplicate-heavy, append, scroll, rewrite, and empty inputs.
- [ ] A 5000-line duplicate-heavy input completes within the agreed linear-time budget.
- [ ] Masking the configured tail lines lets an otherwise volatile screen still produce a valid overlap.
- [ ] A zero-overlap snapshot first recovers pure insert blocks; only after the configured consecutive threshold is the baseline rebuilt.
- [ ] Stability releases only a prefix, delays the last non-empty line one extra round, and resets a changed line and everything after it.
- [ ] Empty feeds advance candidate counters, and reset clears the candidate block.
- [ ] Baseline initialization produces no output.
