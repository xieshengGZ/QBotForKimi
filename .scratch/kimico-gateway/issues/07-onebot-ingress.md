# 07: OneBot ingress and inbound queue

**What to build:** A valid private text event from the main QQ reaches the inbound queue exactly once and in request order. Unauthorized, group, duplicate, malformed, and oversized requests are rejected or silently ignored according to the HTTP contract, and a full inbound queue produces a busy reply in the control lane.

**Blocked by:** 02 (Complete configuration contract), 06 (Outbound bus and sender).

**Status:** ready-for-agent

- [ ] A valid event returns HTTP 200 and reaches the callback with message ID, user ID, concatenated text, and has-text state.
- [ ] A bad or missing bearer token returns 401 and does not reach the callback.
- [ ] Wrong method, wrong path, missing length, negative length, invalid JSON, and a non-list message each return their documented status code.
- [ ] An oversized body returns 413 with the connection closed and without reading the full body.
- [ ] Group messages, other users, and duplicate message IDs return 200 and do not reach the callback.
- [ ] Text segments are concatenated without separators; malformed text segments are skipped; no valid text segment sets has-text false.
- [ ] A full inbound queue drops the event and puts the busy reply in the control lane.
- [ ] Two requests processed by the single-threaded ingress reach the queue in request order.
