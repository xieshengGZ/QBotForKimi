# 01: Walking skeleton — minimal config startup

**What to build:** An operator can start the gateway with only the three required settings, see one consistent log stream, receive all configuration errors in one exit, and stop it with SIGTERM/SIGINT without a traceback. This is the first end-to-end path from the command line through configuration and logging to process exit.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] Starting with a minimal configuration logs a startup line in the standard timestamp/level/logger/message format.
- [x] SIGTERM and SIGINT both stop the process cleanly with exit code 0.
- [x] A configuration missing any required field exits with code 2 and prints every missing field, one per line.
- [x] A malformed TOML file exits with code 2 and a clear parse error.
- [x] Editing the configuration file while the process runs does not change behavior until restart.

## Comments

### W1-1 · 2026-11-02

- Commit: `e9f535f0afc152a819c10e448ad94cedeba5d3bd` on `feat/01-walking-skeleton` (`feat(runtime): minimal config startup, logging, and signal shutdown (01)`).
- Changed files: `kimico_gateway/__init__.py`, `kimico_gateway/config.py`, `kimico_gateway/__main__.py`, `tests/test_walking_skeleton.py`.
- Commands run from `/home/ubuntu/qq/.worktrees/01`:
  - `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` → 6 passed (repeated 5×, stable).
  - Manual: minimal config + SIGTERM → startup/shutdown lines in `%(asctime)s %(levelname)s %(name)s %(message)s`, exit 0, empty stderr.
  - Manual: missing all required fields → exit 2, stderr lists `server.access_token`, `auth.main_qq`, `onebot.access_token` one per line.
  - Manual: malformed TOML → exit 2, `failed to parse <path>: ...`; missing file and default path `/etc/kimico-gateway/config.toml` → exit 2.
- Assumptions / deferred:
  - Ticket 01 only. The full config contract (all sections/fields, ranges, regex compilation, unknown-key warnings, token placeholder/empty rejection, token masking, non-loopback warning, deep immutability) is intentionally left to ticket 02; `Config` currently exposes only the three required fields plus `logging.level`.
  - `logging.level` defaults to `INFO` and is case-normalized; invalid levels are a `ConfigError`.
  - Logger name is the explicit `kimico_gateway.__main__` (using `__name__` would log as `__main__` under `python3 -m`).
  - More than one CLI argument prints usage and exits 2 (not an acceptance item).
  - Tests drive the real entry point in subprocesses with `PYTHONPATH` set to the worktree root; no runtime third-party dependencies were added.

### 控制器验收 · 2026-11-02

- 合并：`e9f535f`（`git merge --ff-only feat/01-walking-skeleton`，main 快进）。
- 树比对：`git diff --stat e9f535f HEAD` 为空，窗口测试结果直接采纳。
- 验收命令：从 worktree 运行 `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` → 6 passed；`python -m compileall -q kimico_gateway` 通过。
- 范围检查：仅新增 `kimico_gateway/{__init__,config,__main__}.py` 与 `tests/test_walking_skeleton.py`；无 `.skip`/`xfail`；无运行时第三方依赖；未改 `docs/prd/**`、全局文档或其他票。
- 结论：5 条验收项全部通过，ticket 01 `done`。
