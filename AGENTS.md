## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature>/`; the GitHub remote is for code only. See `docs/agents/issue-tracker.md`.

### Triage labels

Five-role triage vocabulary recorded in `Status:` lines: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` at the repo root and ADRs in `docs/adr/`. See `docs/agents/domain.md`.

### Ticket controller

按波次推进 `.scratch/kimico-gateway/issues/` 下的 ticket：扫描 `Blocked by`、每波最多 3 个窗口、大票独占、每票开始与结束更新 `docs/output/全局Ticket说明.md`；Step 0 真实 TUI 门禁未过时不开实现窗口。见 `.kimi-code/skills/ticket-controller/SKILL.md`。
