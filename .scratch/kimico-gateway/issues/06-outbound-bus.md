# 06: Outbound bus and sender

**What to build:** Control replies and kimico output can be sent through one outbound path where control messages are preferred, output overflow is handled without dropping control messages, stale output is discarded after clear or mirror-off, and a failed send is retried once before being dropped.

**Blocked by:** 02 (Complete configuration contract).

**Status:** in-progress

- [ ] The bus returns a queued control message before any queued output message.
- [ ] A full output lane drops the oldest output and appends at most one overflow notice.
- [ ] Control messages are never dropped because the output lane is full, and `clear-output` leaves the control lane intact.
- [ ] Output items carry a generation; the sender drops an item whose generation is stale before send and before retry.
- [ ] Control items carry no generation and are not discarded for staleness.
- [ ] The sender retries one time after an interruptible two-second wait, then drops the segment.
- [ ] Output and control lanes use their configured send intervals without blocking shutdown.
- [ ] Send failure logs contain length and reason, never message content.
