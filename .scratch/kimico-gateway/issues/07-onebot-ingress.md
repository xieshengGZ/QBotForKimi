# 07: OneBot ingress and inbound queue

**What to build:** A valid private text event from the main QQ reaches the inbound queue exactly once and in request order. Unauthorized, group, duplicate, malformed, and oversized requests are rejected or silently ignored according to the HTTP contract, and a full inbound queue produces a busy reply in the control lane.

**Blocked by:** 02 (Complete configuration contract), 06 (Outbound bus and sender).

**Status:** done

- [x] A valid event returns HTTP 200 and reaches the callback with message ID, user ID, concatenated text, and has-text state.
- [x] A bad or missing bearer token returns 401 and does not reach the callback.
- [x] Wrong method, wrong path, missing length, negative length, invalid JSON, and a non-list message each return their documented status code.
- [x] An oversized body returns 413 with the connection closed and without reading the full body.
- [x] Group messages, other users, and duplicate message IDs return 200 and do not reach the callback.
- [x] Text segments are concatenated without separators; malformed text segments are skipped; no valid text segment sets has-text false.
- [x] A full inbound queue drops the event and puts the busy reply in the control lane.
- [x] Two requests processed by the single-threaded ingress reach the queue in request order.

## Comments

- 2026-11-02 W4-2：commit `02c068f`（分支 `feat/07-onebot-ingress`）。
  - 改动：`kimico_gateway/onebot.py` 新增 `PrivateMessageEvent`、`DedupTable`、`extract_text`、`OnebotServer`，保留原有 `OnebotAPIError`/`send_private_msg`；新增 `kimico_gateway/input.py`（仅 `InboundQueue`）；新增 `tests/test_onebot_ingress.py`（32 个用例）、`tests/test_inbound_queue.py`（4 个用例）。
  - 自测命令（在 `/home/ubuntu/qq/.worktrees/07` 运行）：`/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q`
  - 结果：`159 passed in 17.08s`（基线 123 + 新增 36）。
  - 假设/未完成：`OnebotServer` 在构造时绑定监听端口，并额外暴露 `server_address` 供测试获取随机端口；本票不含注入 worker、`mirror.py`/`router.py`/`__main__` 接线；队列满 busy 的文字与 `put_control` 接线只在测试回调中覆盖，待后续接线票落地。

### 控制器验收 · 2026-11-02

- 合并：`553d70e`（窗口提交 `02c068f`，rebase 后快进）。
- worktree：`pytest tests/ -q` → 159 passed（含 36 个 ingress/queue 测试）；main 波次全量 208 passed；`compileall` 通过。
- 范围：`kimico_gateway/onebot.py`（ingress + 发送保留）、`kimico_gateway/input.py`（仅 `InboundQueue`）、`tests/test_onebot_ingress.py`、`tests/test_inbound_queue.py`；未改 `router.py`/`mirror.py`、其他模块、PRD、全局文档；无 `.skip`/`xfail`、无运行时依赖。
- 关键点：405/404/411/400/413/401/400；413 不读 body 且 `Connection: close`；群/他人/重复静默 200；`on_event` 异常仍 200；单线程 `HTTPServer`；队列满 busy 进控制 lane；两请求顺序一致。
- 结论：8 条验收项全部通过，ticket 07 `done`。
