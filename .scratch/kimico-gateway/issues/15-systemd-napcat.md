# 15: systemd and NapCat deployment

**What to build:** An operator can install the gateway as a systemd service that restarts on failure but not on configuration errors, leaves tmux and kimico alive across a gateway restart, reads its configuration as the service user, finds kimico through the service environment, and reaches a pinned NapCat container on loopback.

**Blocked by:** 14 (Health checks and graceful shutdown).

**Status:** done

- [x] The systemd unit uses `KillMode=process`, `Restart=on-failure`, a prevented restart on exit code 2, and start-limit settings.
- [x] `systemctl restart` leaves the tmux/kimico pane PID unchanged.
- [x] The configuration file is owned by the service user with mode 600 and is readable by that user.
- [x] The service has HOME, TERM, PATH, and loopback `no_proxy` set, and an absolute kimico command or an effective PATH.
- [x] The NapCat container uses a pinned image version or digest, host networking, and loopback-only API/report ports.
- [x] OneBot is configured for array message format, matching tokens, and the documented report URL.
- [x] The deployment guide documents the main QQ versus gateway QQ distinction, risk, backup, upgrade, and uninstall.

## Comments

- W9-1 implementation complete.
- Commit: `5cbc25b32c0552a5a29443a375f0f4a59704fd16` (`feat(deploy): systemd and NapCat deployment (15)`).
- Files:
  - `deploy/kimico-gateway.service` (PRD 30 §2.3 unit: `KillMode=process`, `Restart=on-failure`, `RestartPreventExitStatus=2`, `RestartSec=3`, `StartLimitIntervalSec=60`, `StartLimitBurst=5`, `User=ubuntu`, `WorkingDirectory=/opt/kimico-gateway`, HOME/TERM/PATH/PYTHONUNBUFFERED/no_proxy, `/usr/bin/python3.12` entrypoint, journal output).
  - `deploy/config.toml.example` (full valid config; token placeholders, loopback server, absolute `kimico_command`, `/var/lib/kimico-gateway/state.json`).
  - `deploy/napcat/compose.yaml` (pinned `mlikiowa/napcat-docker:v4.8.0`, `network_mode: host`, `restart: unless-stopped`, `/opt/napcat/config` volume, no host `ports:`).
  - `deploy/napcat/onebot11.template.json` (array format, `127.0.0.1:3000` API, `http://127.0.0.1:8080/cq` report, tokens matching the gateway config).
  - `deploy/install.sh` / `deploy/uninstall.sh` (idempotent; dirs, package/config install, 600 + service owner, daemon-reload, enable --now; `--no-start`; uninstall keeps config/state unless `--purge`).
  - `deploy/README.md` (main vs gateway QQ, risk/封号, backup, upgrade, uninstall, permissions, HOME/TERM/PATH/no_proxy, NapCat login/OneBot, loopback ports).
  - `tests/test_deploy_artifacts.py` (new, 18 static tests; no root/network/service).
  - `docs/output/deploy-verification.md` (real systemd verification record).
- Verification (worktree `/home/ubuntu/qq/.worktrees/15`, `/home/ubuntu/qq/.venv/bin/python`):
  - `-m pytest tests/test_deploy_artifacts.py -q` → 18 passed.
  - `-m pytest -q` → 388 passed in 21.90s (baseline 370 + 18 new).
  - `bash -n deploy/install.sh && bash -n deploy/uninstall.sh` → ok.
- Real systemd verification (systemd 255, Python 3.12.3, tmux 3.4; 8080 free; existing `kimi02`/`qq-6`/`test` tmux sessions and `shotsmith-*` containers untouched):
  - Installed with `sudo KIMICO_GATEWAY_CONFIG_SOURCE=<test config> ./deploy/install.sh`; service became `active`; created tmux session `kimico`.
  - Acceptance 2: before `pane_pid=2301971`, after `systemctl restart` `pane_pid=2301971`, `pane_dead=0`, gateway main PID changed `383965 → 384167`.
  - Config: `stat` → `600 ubuntu:ubuntu`; `sudo -u ubuntu test -r ...` → PASS.
  - Environment: `systemctl show -p Environment` → `HOME=/home/ubuntu TERM=xterm-256color PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin PYTHONUNBUFFERED=1 no_proxy=127.0.0.1,localhost`.
  - Exit code 2: invalid TOML + `systemctl restart` → `ActiveState=failed`, `ExecMainStatus=2`, `NRestarts` stayed `0` after > RestartSec; journal shows `status=2/INVALIDARGUMENT` and no automatic restart. Restored config and `systemctl start` → active again with the same pane PID.
  - Idempotency: second `install.sh --no-start` kept the config hash unchanged and the active service running.
  - Listener: `127.0.0.1:8080` only.
  - Cleanup: `systemctl stop`, `systemctl disable`, `tmux kill-session -t =kimico`; `/etc/kimico-gateway`, `/var/lib/kimico-gateway`, `/opt/kimico-gateway` preserved for ticket 16. Other tmux sessions and `shotsmith-*` containers confirmed untouched.
- Unfinished / assumptions:
  - NapCat was **not** started at runtime: port 3000 is occupied by unrelated `shotsmith-web-1`, and the ticket forbids touching it. NapCat/OneBot acceptance is static-only; runtime login/real-QQ checks remain for ticket 16 or a networked host.
  - No Docker Hub network access, so `v4.8.0` could not be verified with `docker manifest inspect`; README/verification record mark it as an assumption and document digest pinning.
  - A tmux server for `ubuntu` already existed, so the service joined that server rather than starting one inside the unit cgroup. The restart/PID promise passed, but the specific "tmux server born in the gateway cgroup" branch was not isolated (no existing session was disturbed).
  - Ticket Status and acceptance checkboxes left unchanged for the controller.

- 2026-11-02 W9 controller: verified the tested tree is byte-identical for `deploy/`, `kimico_gateway/`, and `tests/`, merged `5cbc25b` (feature) to main with `git merge --ff-only`. Wave-end on main: `/home/ubuntu/qq/.venv/bin/python -m pytest -q` → 388 passed; `compileall -q kimico_gateway` → ok; both deploy scripts pass `bash -n`. Controller re-checked the host after cleanup: service `inactive`/`disabled`, config `600 ubuntu:ubuntu` readable, only `kimi02`/`qq-6`/`test` tmux sessions remain, port 8080 free, `shotsmith-*` containers untouched. NapCat runtime remains blocked by the port-3000 conflict and is recorded as an environment limitation for ticket 16.

