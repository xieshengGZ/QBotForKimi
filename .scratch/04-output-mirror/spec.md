# 04 · Output Capture and Mirror

Status: ready-for-agent
Category: enhancement

## Problem Statement

The operator wants to read kimico’s answers on the phone without seeing the terminal’s redraw noise, duplicated input echo, or a replay of old output. kimico is an interactive TUI, so the gateway must distinguish genuinely new, stable output from status-line churn, keep long answers readable, and never let output flooding drop control replies.

## Solution

Build the output path: poll the tmux pane, diff snapshots with a linear overlap algorithm, mask volatile tail lines and recover inserted lines, release only stable lines, suppress input echo conservatively, batch output into readable chunks, and deliver it through a two-lane outbound bus. The mirror switch controls whether output is pushed; generation counters make clear and mirror-off semantics reliable.

## User Stories

1. As an operator, I want the phone to receive only new output since the last baseline, so that restarting or reconnecting does not replay old answers.
2. As an operator, I want the first capture after startup to establish a baseline without sending the current screen, so that the phone is not flooded with history.
3. As an operator, I want output held until it is stable, so that progress bars and partial lines do not appear as final answers.
4. As an operator, I want the last non-empty line of a block delayed one extra round, so that a final line is not pushed while it is still changing.
5. As an operator, I want blank lines filtered by default, so that terminal padding does not create empty QQ messages.
6. As an operator, I want long answers split into chunks of at most 1500 characters, so that they can be delivered without truncation.
7. As an operator, I want hard-split only for a single line longer than the limit, so that ordinary multi-line answers are not cut mid-line.
8. As an operator, I want chunks to preserve line order, so that an answer reads correctly across messages.
9. As an operator, I want output batched after a short quiet window, so that a long answer does not arrive as dozens of tiny messages.
10. As an operator, I want the batch flushed when it reaches a size limit, so that a continuously streaming answer is not held forever.
11. As an operator, I want the terminal’s volatile bottom lines to be masked during diffing, so that a spinner or status bar does not make every poll look like a total screen replacement.
12. As an operator, I want a changed bottom line not to cause the whole screen to be discarded, so that the answer above it is still delivered.
13. As an operator, I want `k=0` to first try recovering inserted lines, so that a single redraw does not drop a real answer.
14. As an operator, I want the baseline rebuilt only after several consecutive failures, so that a transient redraw does not lose output permanently.
15. As an operator, I want injected text’s echo suppressed, so that I do not see my own message twice when mirror is on.
16. As an operator, I want a legitimate answer line that happens to match my injected text not to be silently swallowed; the current trade-off is documented, and the implementation must prefer not to drop whole blocks.
17. As an operator, I want `#mirror off` to stop phone output immediately, clear the output lane, and preserve control replies.
18. As an operator, I want `#mirror on` to resume with only new output, so that old content is not replayed.
19. As an operator, I want `#clear` to reset the baseline and drop old-generation output, so that a fresh view is possible without losing kimico’s context.
20. As an operator, I want one stale in-flight network request to be acceptable after clear/off, but no further old-generation segments to be sent.
21. As an operator, I want an overflow notice when output is produced faster than it can be sent, so that I know some content was dropped.
22. As an operator, I want control replies and alarms to be prioritized over output, so that I can still get status when output is flooding.
23. As an implementer, I want a linear overlap algorithm, so that 5000-line snapshots at 300ms do not consume quadratic CPU.
24. As an implementer, I want capture calls to time out, so that a wedged tmux server cannot hang the monitor thread.
25. As an implementer, I want output segments tagged with a generation, so that the sender can drop stale segments after clear/off.
26. As an implementer, I want a control lane and an output lane with different drop policies, so that control messages are never dropped by output overflow.
27. As an implementer, I want the mirror switch persisted through the state store, so a gateway restart restores the operator’s choice.

## Implementation Decisions

- Capture uses `tmux capture-pane` with a fixed argument list, joined wrapped lines, a configurable history depth, and a five-second timeout. Empty output is an empty list; a non-zero exit or timeout is a capture error that skips the round.
- The diff uses a KMP prefix-function over the current snapshot, a sentinel, and the previous snapshot to find the largest suffix/prefix overlap in linear time. The result is clamped to both input lengths.
- Before diffing, the last N lines can be replaced with a volatile sentinel. N is configurable and defaults to zero. The masked lines do not participate in overlap matching, but they remain part of the new-line slice so that a stable tail can still be evaluated.
- On `k=0`, the implementation first looks for pure insert blocks with a sequence matcher. If inserts are found, they become the new lines and the baseline advances. If not, a consecutive-zero counter increments; only after the configured threshold is the baseline rebuilt and the fragment discarded.
- Stability is line-based. A candidate line’s count increments only when its position and content are unchanged; a change resets that line and all following lines. Release is prefix-only. The last non-empty line needs one extra stable round.
- After stability, the pipeline checks the mirror switch and generation, filters ignored lines, applies echo suppression, appends to a batch, and flushes when the quiet window expires or the batch size is reached.
- Echo suppression is line-based and may match any line in the filtered block, not just the first. It normalizes whitespace and can strip configured prompt prefixes. On a match, only that line is removed and the oldest registered echo is consumed. If nothing matches, the block is unchanged. The known false-positive case is documented: a legitimate answer line equal to the injected text can be removed.
- The echo queue is FIFO, expires after ten seconds, is pruned on register and suppress, and is capped at 50 entries.
- The mirror switch is persisted through the state store. `#mirror off` clears only the output lane; control messages are untouched. The output generation increments on off and clear.
- The outbound bus has a control lane and an output lane. The control lane is unbounded in the hard sense, logs an error above a soft maximum of 100, and is never cleared by output operations. The output lane is bounded by configuration; when full it drops the oldest output and appends a single overflow notice if one is not already pending.
- Sending always drains the control lane first. Each item carries an optional generation. Output items whose generation is stale are discarded before send and before retry. Control items have no generation and are never discarded for staleness.
- The sender retries one time after two seconds, using an interruptible wait, and drops the segment after the second failure. It logs only length and reason, never content.
- The sender waits the configured interval between output segments and a shorter fixed interval between control segments. A single in-flight network request cannot be recalled; that is the documented boundary.
- Output is chunked by whole lines where possible. A single line longer than the chunk limit is hard-split. Newlines count toward the limit. Input order is preserved.

## Testing Decisions

A good test asserts externally observable behavior: captured lines in, bus items out, sender calls, and the final QQ text. Algorithmic functions should be tested against independent reference implementations, not by reproducing their internal steps.

**Seam:** the highest seam is the monitor/pipeline/outbound pipeline with fake capture and fake sender. Pure algorithm functions (overlap, mask, recover, stability, chunking, echo) are tested at their public function seam because they are the decision-rich core.

- Overlap tests compare KMP results with a brute-force definition on random sequences, including duplicates, appends, scrolls, rewrites, empty inputs, and a 5000-line performance case.
- Mask and recovery tests cover a changed tail, an inserted block, a pure replace, and an empty screen.
- Stability tests cover basic release, tail delay, changed lines, prefix-only release, empty feeds, reset, and the extra-round rule.
- Echo tests cover exact line match, prompt prefix, multi-line block, two identical injections, expiry, FIFO head blocking, no partial match, and the documented false-positive case.
- Chunk tests cover aggregation, exact limit, hard split, flush boundaries, and empty input.
- Bus tests cover control priority, control not dropped by output overflow, output overflow notice, notice repeat, clear-output-only, and concurrent put/get atomicity.
- Sender tests cover success, first-failure retry, second-failure drop, stale generation before send and before retry, interval behavior, and interruptible retry.
- Pipeline tests cover mirror off, stale generation, quiet-window batch, max-batch flush, filter-before-echo order, and no output for an empty filtered block.
- There is no existing test suite; the prior art is the product PRD’s capture and mirror test tables.

## Out of Scope

- QQ ingress, command parsing, or inbound injection.
- tmux session creation, pane recovery, or deployment.
- Real kimico TUI validation; the real-TUI gate belongs to the delivery spec.
- Changing the current any-line echo policy; only the known limitation is documented.

## Further Notes

- This spec depends on Spec 01 for configuration and Spec 03 for control-lane usage and command side effects.
- The capture algorithm is the highest-risk area. The delivery spec’s real-kimico gate must run before this spec is considered production-ready.
- The output lane’s drop policy is intentionally lossy; control messages are the reliable lane.
