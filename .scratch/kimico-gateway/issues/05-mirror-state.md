# 05: Mirror state persistence

**What to build:** An operator’s mirror choice survives a gateway restart. A missing state file starts as mirror off; a corrupt or wrong-type file falls back to off with a warning; writes are atomic, serialized, and leave no temporary residue; concurrent saves cannot produce a half-written file.

**Blocked by:** 02 (Complete configuration contract).

**Status:** done

- [x] A missing state file loads as mirror off and logs at INFO.
- [x] A valid true/false state file loads the matching mirror value.
- [x] Corrupt JSON, a non-object root, or a non-boolean mirror value falls back to off with a WARNING.
- [x] Extra keys in the state file are ignored.
- [x] Saving creates parent directories when needed and round-trips through load.
- [x] Saves use a unique temporary file, flush and fsync before an atomic replace, and leave no temporary residue.
- [x] Two concurrent saves always leave a valid JSON file and no temporary residue.
- [x] A save failure is logged at ERROR and does not raise or stop the process.

## Comments

- 2026-11-02 W3-2:
  - Commit: `df48a47` on `feat/05-mirror-state` (baseline `d51e70e`).
  - Files: `kimico_gateway/state.py` (new), `tests/test_state.py` (new).
  - Command: `/home/ubuntu/qq/.venv/bin/python -m pytest tests/ -q` from `/home/ubuntu/qq/.worktrees/05`.
  - Result: `71 passed` (25 new state tests + existing 46); `python -m compileall -q kimico_gateway` passed.
  - Acceptance: all 8 items covered. Missing file → INFO + `False`; valid true/false round-trip; corrupt JSON / non-object root / non-bool mirror → WARNING + `False`; extra keys and missing key ignored without warning; `save` creates parent dirs and round-trips; unique `state.*.tmp` via `mkstemp`, flush + `os.fsync` before `os.replace`, no temp residue; lock-serialized concurrent saves with a reader thread asserting every observed file is valid JSON; save failure (target is a directory) logs ERROR, does not raise, and removes the temp file.
  - Assumptions / not done:
    - `load` treats any `OSError`/`ValueError` (including a directory target or invalid UTF-8) as damaged: WARNING + `False`; a missing parent directory surfaces as `FileNotFoundError` and is treated as first start (INFO).
    - Path is resolved to an absolute directory for dir creation and `mkstemp`; `os.replace` still targets the configured path verbatim.
    - Startup cleanup of stale `state.*.tmp` files was not added (optional per `11-mod-state.md` §4); each save uses a fresh unique temp name.
    - No wiring into `__main__` or other modules (ticket 13); no runtime third-party dependency added.

### 控制器验收 · 2026-11-02

- 合并：`7ec7f28`（`git merge --ff-only feat/05-mirror-state`，rebase 后快进）。
- worktree：`pytest tests/ -q` → 71 passed（含 25 个 state 测试）；main 波次全量 123 passed；`compileall` 通过。
- 范围：仅 `kimico_gateway/state.py`、`tests/test_state.py`；无 `.skip`/`xfail`；无运行时第三方依赖；未改其他模块、PRD、全局文档。
- 关键点：唯一临时文件 + flush + fsync + `os.replace`；进程内锁串行化；缺失 INFO、损坏/类型 WARNING、写失败 ERROR 不抛；额外键忽略。
- 结论：8 条验收项全部通过，ticket 05 `done`。
