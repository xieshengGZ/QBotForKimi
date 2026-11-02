# 06: Outbound bus and sender

**What to build:** Control replies and kimico output can be sent through one outbound path where control messages are preferred, output overflow is handled without dropping control messages, stale output is discarded after clear or mirror-off, and a failed send is retried once before being dropped.

**Blocked by:** 02 (Complete configuration contract).

**Status:** done

- [x] The bus returns a queued control message before any queued output message.
- [x] A full output lane drops the oldest output and appends at most one overflow notice.
- [x] Control messages are never dropped because the output lane is full, and `clear-output` leaves the control lane intact.
- [x] Output items carry a generation; the sender drops an item whose generation is stale before send and before retry.
- [x] Control items carry no generation and are not discarded for staleness.
- [x] The sender retries one time after an interruptible two-second wait, then drops the segment.
- [x] Output and control lanes use their configured send intervals without blocking shutdown.
- [x] Send failure logs contain length and reason, never message content.

## Comments

- 2026-11-02 W3-3:
  - Commit: `ae085078e8ef41f4b7799c465ae3b5fd4198c2e9` on `feat/06-outbound-bus` (baseline `d51e70e`).
  - Files: `kimico_gateway/mirror.py` (new, outbound only), `kimico_gateway/onebot.py` (new, send only), `tests/test_outbound_bus.py` (new).
  - Command: `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` from `/home/ubuntu/qq/.worktrees/06`.
  - Result: `64 passed` (18 new outbound tests + existing 46); no skips, no third-party runtime dependency.
  - Acceptance: all 8 items covered. Bus: control-first `get`, bounded bulk with oldest-drop + identity-tracked single `OVERFLOW_NOTICE`, control lane unbounded with soft-max ERROR, `clear_bulk` leaves control lane and notice state reset. Sender: stale generation checked before send and before retry (control items have `generation=None`), one retry after `stop_event.wait(2.0)`, final failure logs length + reason only, bulk uses `send_interval`, control uses `control_interval`, `get(timeout=0.5)` + interruptible waits bound shutdown. OneBot: `urllib` POST `/send_private_msg` with `auto_escape=true`, 3s default timeout, proxy bypass, 200 + `retcode==0` success, failures normalized to `OnebotAPIError`.
  - Assumptions / not done:
    - Overflow order follows PRD §5 literally: after dropping to make room, the notice is appended, then the new item; if this exceeds capacity the oldest items are dropped again. Capacity 3 + 5 puts yields `[OVERFLOW_NOTICE, msg-4, msg-5]`.
    - PRD §6 pseudocode says `if not stop_event.wait(2): continue` but its comment says shutdown abandons the retry; implemented as `if stop_event.wait(2): continue` so a set stop event correctly skips the retry.
    - `run_sender_loop` takes `epoch` as an object exposing only `.current()`, so tests use a fake epoch; no `OutputEpoch` implementation (ticket 11).
    - `mirror.py` contains only `Lane`, `OutboundItem`, `OutboundBus`, `run_sender_loop`; `onebot.py` contains only `OnebotAPIError` and `send_private_msg`. No server, switch, epoch, pipeline, capture, or command wiring.
    - OneBot tests use a local loopback fake HTTP server only; no real NapCat or external network access.

### 控制器验收 · 2026-11-02

- 合并：`0d93c81`（`git merge --ff-only feat/06-outbound-bus`，rebase 后快进）。
- worktree：`pytest tests/ -q` → 64 passed（含 18 个出站测试）；main 波次全量 123 passed；`compileall` 通过。
- 范围：仅 `kimico_gateway/mirror.py`、`kimico_gateway/onebot.py`（发送部分）、`tests/test_outbound_bus.py`；无 `.skip`/`xfail`；无运行时第三方依赖；未改其他模块、PRD、全局文档。
- 记录：PRD §6 伪代码 `if not stop_event.wait(2): continue` 与“停机时放弃重试”的注释矛盾；窗口按注释实现（wait 返回 True 表示停机，跳过重试），符合行为规约。
- 结论：8 条验收项全部通过，ticket 06 `done`。
