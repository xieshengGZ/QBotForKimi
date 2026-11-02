# 03 · Inbound QQ Control

Status: ready-for-agent
Category: enhancement

## Problem Statement

The operator needs to send ordinary text from the main QQ account into kimico and to issue control commands from the same chat. The gateway must reject unauthorized and unsupported events, preserve the order of accepted messages, avoid injecting control commands into kimico, and keep control replies reliable even when output is flooding the phone.

## Solution

Build the inbound path: a single-threaded OneBot HTTP ingress, strict event filtering and deduplication, a bounded FIFO, a serial input worker, and named-buffer tmux injection. The worker dispatches control commands through the router, writes replies to the control lane, and injects ordinary text unchanged. The path also defines empty-message handling, the `##` literal-hash escape, and injection failure behavior.

## User Stories

1. As an operator, I want only private messages from the configured main QQ to be accepted, so that no other QQ account can control the machine.
2. As an operator, I want group messages and messages from other accounts silently ignored with HTTP 200, so that NapCat does not retry them.
3. As an operator, I want non-text messages to receive “仅支持文本消息”, so that I know why nothing was injected.
4. As an operator, I want duplicate message IDs to be ignored, so that NapCat retries do not inject the same text twice.
5. As an operator, I want text segments to be concatenated in order, so that a message split across segments is injected as one text.
6. As an operator, I want an empty or whitespace-only message to be silently ignored, so that accidental blank messages do not submit blank lines to kimico.
7. As an operator, I want ordinary messages injected unchanged, so that quotes, newlines, and leading/trailing spaces behave as I sent them.
8. As an operator, I want multi-line messages to be pasted into kimico as one paste and submitted with one Enter, so that lines are not submitted separately by the gateway.
9. As an operator, I want the gateway to use a named tmux buffer and delete it after paste, so that my own tmux paste buffer is not overwritten.
10. As an operator, I want a message beginning with `##` to send a literal `#` to kimico, so that I can send text that would otherwise be interpreted as a control command.
11. As an operator, I want `##` recognized only at the start of the message, so that leading spaces remain part of ordinary text.
12. As an operator, I want `#mirror on` and `#mirror off` to toggle phone output and receive a reply, so that I can control noise from the phone.
13. As an operator, I want `#status` to return tmux state, kimico state, mirror state, inbound queue, control queue, output queue, last output time, and uptime, so that I can diagnose the gateway from the phone.
14. As an operator, I want `#clear` to clear pending output and reset the output baseline without touching kimico’s context, so that old output does not reappear.
15. As an operator, I want unknown `#` commands to return the usage text and not be injected, so that typos do not become kimico input.
16. As an operator, I want control replies to be placed in the control lane, so that they are not dropped when output floods the output lane.
17. As an operator, I want injection failures to return “注入失败，请稍后重试” and be logged, so that I know the message did not reach kimico.
18. As an operator, I want a full inbound queue to return “当前繁忙，请稍后重试” and drop the new event, so that the gateway does not grow without bound.
19. As an operator, I want consecutive messages injected in arrival order, so that my instructions are not reordered.
20. As an implementer, I want a single-threaded HTTP server, so that event enqueue order is deterministic without a reorder buffer.
21. As an implementer, I want strict request validation before enqueue, so that malformed requests never reach the worker.
22. As an implementer, I want the worker to isolate per-event exceptions, so that one bad event cannot kill the input thread.
23. As an implementer, I want all tmux injection calls to have a timeout, so that a wedged tmux server cannot block input forever.
24. As an implementer, I want injection errors to exclude the message text, so that logs do not leak chat content.
25. As an implementer, I want the command parser to be a pure function, so that command behavior is testable without tmux or QQ.

## Implementation Decisions

- The OneBot ingress is a single-threaded HTTP server. Requests are processed in connection order. A request is rejected with 405 for non-POST, 404 for a wrong path, 411 for a missing length, 400 for an invalid or negative length, 413 for an oversized body, 401 for a bad token, and 400 for invalid JSON or a missing/invalid message list.
- Authentication compares the bearer token in constant time. Event filtering happens after authentication. Non-main-QQ, group, and duplicate events return 200 with no body so NapCat does not retry.
- Deduplication is a FIFO of the last 200 message IDs. A repeated ID is silently ignored. IDs older than the window may be processed again; that is a documented limit.
- Text extraction concatenates every text segment whose data text is a string, with no separator. A text segment with missing or non-string data text is skipped. If no valid text segment exists, the event is marked as having no text.
- The inbound queue is a FIFO with capacity 50. The HTTP layer attempts a non-blocking put; on failure it puts the busy reply in the control lane and drops the event.
- The input worker processes one event at a time. The order is: no-text reply; whitespace-only ignore; `##` literal injection; control command dispatch; ordinary injection.
- The `##` rule matches only at column zero. The first `#` is removed and the remainder is injected unchanged. A leading space before `##` makes it ordinary text.
- Ordinary injection uses a unique named tmux buffer, a bracketed paste that preserves linefeeds, one Enter, and a five-second timeout. The buffer is deleted after paste or on failure. NUL characters are rejected before invoking tmux.
- Control parsing is case-insensitive, trims surrounding whitespace, splits on arbitrary whitespace, and accepts exactly the documented token counts. `#mirror`, `#mirror onn`, `#mirror on now`, and any other unrecognized `#` text are unknown commands.
- Command side effects are ordered: mirror off sets the switch, increments the output generation, clears the output lane, then queues the reply; clear increments the generation, clears the output lane, requests a baseline reset, then queues the reply. Control replies are never cleared by these commands.
- The status snapshot reports eight lines in a fixed order and uses the configured output-lane capacity and the fixed control-lane soft maximum.
- Ordinary injection success is silent; there is no ACK. The injected text is registered with the echo suppressor immediately after a successful injection.
- Each event is processed inside a per-event exception boundary. Unexpected exceptions are logged with a traceback and the worker continues.
- The worker never parses or rewrites ordinary text, including `/session`; only the `##` escape and whitespace-only rule modify handling.

## Testing Decisions

A good test drives the public ingress/worker seam and asserts what NapCat would see and what would be injected: HTTP status, reply text, injection argument, queue order, and control-lane placement. Tests must not assert private parser helpers or the exact internal thread scheduling.

**Seam:** one end-to-end inbound seam: raw OneBot event → HTTP response and either injected text or control reply. Fake the tmux injection function and the outbound bus. Use the real queue, dedup table, and worker. A smaller pure-function seam is acceptable for the command parser.

- Ingress tests use a real HTTP server on a random loopback port and a local HTTP client. Cover happy path, wrong method/path, missing/bad/oversized/negative length, bad token, invalid JSON, non-object JSON, missing message, non-list message, group, other user, duplicate, callback exception, and sequential order.
- Extraction tests cover multiple text segments with an image in between, only image, missing data, non-string text, and empty text.
- Worker tests cover no text, empty text, whitespace, `##`, mirror on/off ordering, status, clear, unknown command, ordinary injection, injection failure, exception isolation, and stop within one second.
- Command tests assert exact copy strings and the eight-line status template with fixed time values.
- Injection tests use a fake subprocess runner and assert named buffer, bracketed paste, linefeed preservation, Enter, timeout, buffer cleanup, and that errors do not contain the injected text.
- There is no existing test suite; the prior art is the product PRD’s ingress, input, and router test tables.

## Out of Scope

- Capturing kimico output, diffing, stability, mirror filtering, or echo suppression.
- Sending QQ output beyond placing control replies in the outbound bus.
- tmux session creation, pane detection, or recovery.
- Deployment, NapCat installation, or systemd.

## Further Notes

- This spec depends on Spec 01 for configuration and Spec 02 for the tmux session name and injection target.
- Spec 04 owns the output-lane implementation and the echo suppressor.
- The single-threaded ingress is intentional; do not replace it with a thread-per-request server without revisiting ordering and the acceptance tests.
