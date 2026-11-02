# 02: Complete configuration contract

**What to build:** An operator can configure every supported section and field, and invalid values are reported together in one startup attempt. Empty or placeholder tokens are rejected, tokens are masked in logs, unknown keys and non-loopback listeners produce warnings, and the resulting configuration object is deeply immutable.

**Blocked by:** 01 (Walking skeleton — minimal config startup).

**Status:** done

- [x] Every supported section and field loads from TOML with documented defaults and overrides.
- [x] Wrong types, out-of-range values, invalid regular expressions, and invalid paths are all collected into one exit-code-2 error message.
- [x] Empty, whitespace-only, and placeholder access tokens are rejected.
- [x] Access tokens are masked in every configuration log line.
- [x] Unknown keys and unknown sections produce warnings without blocking startup.
- [x] A non-loopback listener address produces a warning.
- [x] List-valued fields and the configuration object are immutable at runtime.
- [x] Trailing slashes on the OneBot base URL are normalized, and log-level casing is normalized.

## Comments

- 2026-11-02 W2-1:
  - Commit: `e2fb5c9` on `feat/02-config-contract`.
  - Files: `kimico_gateway/config.py`, `kimico_gateway/__main__.py`, `tests/test_config_contract.py`.
  - Command: `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` from `/home/ubuntu/qq/.worktrees/02`.
  - Result: `46 passed` (includes ticket 01's 6 walking-skeleton tests).
  - Acceptance: all 8 items covered; errors aggregate one per line with field paths and `__main__` exits 2; empty/whitespace/`change-me` tokens rejected; tokens masked in config logs; unknown keys/sections and non-loopback hosts warn without blocking; Config/section dataclasses frozen and list fields tuples; `onebot.api_base` trailing slashes stripped; `logging.level` uppercased.
  - Assumptions / not done:
    - Added `summarize_config()` so `__main__` can emit the masked DEBUG summary after `basicConfig`; `load_config` also logs it at DEBUG for direct callers.
    - Warnings raised during `load_config` reach stderr via logging's lastResort before `basicConfig`, then normal logs go to stdout; startup is not blocked.
    - No state store, tmux, OneBot, capture, or mirror implementation (other tickets).

### 控制器验收 · 2026-11-02

- 合并：`e2fb5c9`（`git merge --ff-only feat/02-config-contract`，main 快进）。
- 树比对：`git diff --stat e2fb5c9 HEAD` 为空，窗口测试结果直接采纳。
- 验收命令：从 worktree 运行 `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` → 46 passed（含 ticket 01 的 6 个）；`python -m compileall -q kimico_gateway` 通过。
- 范围检查：改动仅 `kimico_gateway/config.py`、`kimico_gateway/__main__.py`（3 行 DEBUG 摘要）、新增 `tests/test_config_contract.py`；未改 `tests/test_walking_skeleton.py`、`docs/prd/**`、全局文档或其他票；无 `.skip`/`xfail`；无运行时第三方依赖。
- 结论：8 条验收项全部通过，ticket 02 `done`。
