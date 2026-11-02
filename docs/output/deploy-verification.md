# Ticket 15 deployment verification

Date: 2026-11-02
Branch: `feat/15-systemd-napcat`
Baseline: `main @ b2f4eaf` (370 passed)

This file records the real systemd verification for ticket 15. Deployment
artifacts live in `deploy/`; static checks live in
`tests/test_deploy_artifacts.py`.

## 1. Environment

```
systemd 255 (255.4-1ubuntu8.17)
Python 3.12.3 at /usr/bin/python3.12
tmux 3.4
Docker 29.9.0
```

Preconditions observed before the test:

```
$ ss -ltnp | grep ':8080'          # no output: 8080 free
$ tmux ls
kimi02: 1 windows (created Sun Nov  1 08:01:06 2026)
qq-6: 1 windows (created Mon Nov  2 14:15:44 2026) (group qq)
test: 1 windows (created Sun Nov  1 08:06:29 2026)
$ sudo docker ps --format '{{.Names}}|{{.Ports}}'
shotsmith-worker-1|
shotsmith-web-1|0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
shotsmith-postgres-1|0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
shotsmith-redis-1|0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
shotsmith-minio-1|0.0.0.0:9000-9001->9000-9001/tcp, [::]:9000-9001->9000-9001/tcp
```

Port 3000 is already used by `shotsmith-web-1`, so NapCat was **not** started
during this verification (see §8).

## 2. Test configuration and fake kimico

The real `kimico` is not installed in this environment (`command -v kimico`
returns nothing). A fake kimico was placed outside the repository:

```bash
sudo install -d -m 755 -o ubuntu -g ubuntu /opt/kimico-gateway
sudo tee /opt/kimico-gateway/fake-kimico.sh >/dev/null <<'EOF'
#!/usr/bin/env bash
echo "fake-kimico ready"
while IFS= read -r line; do
  echo "you said: $line"
done
EOF
sudo chown ubuntu:ubuntu /opt/kimico-gateway/fake-kimico.sh
sudo chmod 755 /opt/kimico-gateway/fake-kimico.sh
```

A test config was rendered from `deploy/config.toml.example` without modifying
the repository template:

```bash
sed -e 's/REPLACE_WITH_SERVER_ACCESS_TOKEN/verify-server-token/' \
    -e 's/REPLACE_WITH_ONEBOT_ACCESS_TOKEN/verify-onebot-token/' \
    -e 's#/home/ubuntu/.local/bin/kimico#/opt/kimico-gateway/fake-kimico.sh#' \
    deploy/config.toml.example > /tmp/kimico-gateway-verify/config.toml
```

`load_config` accepted the rendered file:

```
config ok 127.0.0.1 8080 /opt/kimico-gateway/fake-kimico.sh
```

## 3. Install

```bash
sudo KIMICO_GATEWAY_CONFIG_SOURCE=/tmp/kimico-gateway-verify/config.toml \
  ./deploy/install.sh
```

Output:

```
installing kimico-gateway for user ubuntu:ubuntu
Created symlink /etc/systemd/system/multi-user.target.wants/kimico-gateway.service -> /etc/systemd/system/kimico-gateway.service.
installed:
  config: /etc/kimico-gateway/config.toml (600 ubuntu:ubuntu)
  state:  /var/lib/kimico-gateway
  code:   /opt/kimico-gateway/kimico_gateway
  unit:   /etc/systemd/system/kimico-gateway.service
```

The service became active and created the `kimico` tmux session:

```
$ systemctl is-active kimico-gateway
active
$ systemctl show kimico-gateway -p ActiveState -p SubState -p ExecMainPID -p NRestarts -p KillMode -p Restart -p RestartPreventExitStatus -p RestartUSec
Restart=on-failure
RestartUSec=3s
RestartPreventExitStatus=2
NRestarts=0
ExecMainPID=383965
KillMode=process
ActiveState=active
SubState=running

$ tmux display-message -p -t kimico '#{pid} #{pane_dead}'
2301971 0

$ sudo journalctl -u kimico-gateway -n 5 --no-pager
... INFO kimico_gateway.session created tmux session 'kimico' with history-limit=50000 and remain-on-exit=on
... INFO kimico_gateway.__main__ gateway ready: 4 threads started
```

## 4. Acceptance item: restart keeps the tmux/kimico pane PID

```bash
BEFORE_PANE=$(tmux display-message -p -t kimico '#{pid}')
BEFORE_MAIN=$(systemctl show kimico-gateway -p ExecMainPID --value)
sudo systemctl restart kimico-gateway
AFTER_PANE=$(tmux display-message -p -t kimico '#{pid}')
AFTER_DEAD=$(tmux display-message -p -t kimico '#{pane_dead}')
AFTER_MAIN=$(systemctl show kimico-gateway -p ExecMainPID --value)
```

Result:

```
before: pane_pid=2301971 main_pid=383965
after:  pane_pid=2301971 pane_dead=0 main_pid=384167
pane_pid_unchanged=yes
main_pid_changed=yes
NRestarts=0
ActiveState=active
SubState=running
```

Journal excerpt:

```
Stopping kimico-gateway.service - kimico QQ remote control gateway...
... gateway shutdown: joining input thread
... gateway shutdown: joining http thread
... gateway shutdown: joining monitor thread
... gateway shutdown: joining sender thread
kimico-gateway.service: Deactivated successfully.
Started kimico-gateway.service - kimico QQ remote control gateway.
... taking over tmux session 'kimico' with remain-on-exit=on
... gateway ready: 4 threads started
```

**PASS**: the pane PID stayed `2301971`, `pane_dead=0`, while the gateway main
PID changed to `384167`.

Environment limitation: an existing tmux server for `ubuntu` was already
running (`kimi02`, `qq-6`, `test`), so the service created its `kimico` session
on that existing server instead of starting a tmux server inside the unit
cgroup. This verifies the operator-visible promise (restart leaves tmux/kimico
alive) but not the specific "tmux server first created by the gateway and then
surviving `systemctl restart`" branch. The unit still carries
`KillMode=process` as required, and a second isolated run would be needed to
exercise that branch without disturbing the user's existing sessions.

## 5. Acceptance item: config ownership, mode, and readability

```bash
$ stat -c '%a %U:%G %n' /etc/kimico-gateway/config.toml \
    /etc/kimico-gateway /var/lib/kimico-gateway /opt/kimico-gateway/kimico_gateway
600 ubuntu:ubuntu /etc/kimico-gateway/config.toml
750 ubuntu:ubuntu /etc/kimico-gateway
750 ubuntu:ubuntu /var/lib/kimico-gateway
755 ubuntu:ubuntu /opt/kimico-gateway/kimico_gateway

$ sudo -u ubuntu test -r /etc/kimico-gateway/config.toml && echo PASS
PASS
$ sudo -u ubuntu head -c 0 /etc/kimico-gateway/config.toml && echo "ubuntu read: PASS"
ubuntu read: PASS
```

**PASS**: mode `600`, owner `ubuntu:ubuntu`, readable by the service user.

## 6. Acceptance item: service environment

```bash
$ systemctl show kimico-gateway -p Environment
Environment=HOME=/home/ubuntu TERM=xterm-256color PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin PYTHONUNBUFFERED=1 no_proxy=127.0.0.1,localhost

$ systemctl show kimico-gateway -p User -p WorkingDirectory
WorkingDirectory=/opt/kimico-gateway
User=ubuntu
```

`/usr/bin/python3.12` exists (the unit's absolute entrypoint):

```
-rwxr-xr-x 1 root root 8020928 Aug 31 18:18 /usr/bin/python3.12
```

**PASS**: HOME, TERM, PATH, loopback `no_proxy`, and an absolute Python
entrypoint are present. `tmux.kimico_command` in the test config is an
absolute path (`/opt/kimico-gateway/fake-kimico.sh`), so kimico is found even
though `kimico` itself is not on PATH.

## 7. Acceptance item: exit code 2 does not restart

The good config was saved first. Then an invalid TOML file was written:

```bash
sudo cp -p /etc/kimico-gateway/config.toml /tmp/kimico-gateway-verify/config.toml.good
printf 'this is not valid TOML [[[\n' | sudo tee /etc/kimico-gateway/config.toml >/dev/null
sudo chown ubuntu:ubuntu /etc/kimico-gateway/config.toml
sudo chmod 600 /etc/kimico-gateway/config.toml
BEFORE=$(systemctl show kimico-gateway -p NRestarts --value)
sudo systemctl restart kimico-gateway
sleep 6                     # longer than RestartSec=3
systemctl is-active kimico-gateway
systemctl show kimico-gateway -p ActiveState -p SubState -p NRestarts -p Result -p ExecMainStatus
```

Result:

```
NRestarts before=0
systemctl restart exit=0
active_state=failed
NRestarts after=0
Result=exit-code
NRestarts=0
ExecMainStatus=2
ActiveState=failed
SubState=failed
```

Journal excerpt:

```
Started kimico-gateway.service - kimico QQ remote control gateway.
python3.12[...]: failed to parse /etc/kimico-gateway/config.toml:
    Expected '=' after a key in a key/value pair (at line 1, column 6)
systemd[1]: kimico-gateway.service: Main process exited, code=exited, status=2/INVALIDARGUMENT
systemd[1]: kimico-gateway.service: Failed with result 'exit-code'.
```

No further "Started ..." line appeared during the 6-second window, and
`NRestarts` stayed `0`. **PASS**: `RestartPreventExitStatus=2` prevents the
automatic restart loop on a configuration error.

The good config was restored and the service started again:

```bash
sudo cp -p /tmp/kimico-gateway-verify/config.toml.good /etc/kimico-gateway/config.toml
sudo chown ubuntu:ubuntu /etc/kimico-gateway/config.toml
sudo chmod 600 /etc/kimico-gateway/config.toml
sudo systemctl start kimico-gateway
```

```
ActiveState=active
SubState=running
NRestarts=0
ExecMainPID=384566
600 ubuntu:ubuntu /etc/kimico-gateway/config.toml
2301971 0
```

The pane PID was still `2301971`, so the failed configuration start did not
destroy the tmux session either.

## 8. Idempotency and loopback

A second install with `--no-start` preserved the config byte-for-byte and left
the active service running:

```
config_hash_unchanged=yes
service before=active after=active
keeping existing configuration: /etc/kimico-gateway/config.toml
service enabled but not started (--no-start).
```

The gateway listened only on loopback:

```
$ ss -ltnp | grep ':8080'
LISTEN 0 5 127.0.0.1:8080 0.0.0.0:* users:(("python3.12",pid=384566,fd=3))
```

## 9. NapCat (static only)

NapCat was not started for this verification because port 3000 is already
occupied by the unrelated `shotsmith-web-1` container. Per the ticket, no
`shotsmith-*` container was stopped or removed, and no attempt was made to bind
NapCat to port 3000.

Static checks cover:

- `deploy/napcat/compose.yaml`: pinned image
  `mlikiowa/napcat-docker:v4.8.0`, `network_mode: host`,
  `restart: unless-stopped`, config volume
  `/opt/napcat/config:/app/napcat/config`, no `ports:` mapping, no `0.0.0.0`.
- `deploy/napcat/onebot11.template.json`: HTTP API on `127.0.0.1:3000`,
  report URL `http://127.0.0.1:8080/cq`, `messagePostFormat: "array"` on both
  server and client, and token placeholders matching
  `deploy/config.toml.example`.

**Assumption**: this build had no network access to Docker Hub, so the tag
`v4.8.0` could not be checked with `docker manifest inspect`. The README marks
this assumption and documents switching to a digest. Runtime NapCat testing
(login, OneBot connectivity, real QQ messages) remains for ticket 16 or a
networked deployment.

## 10. Cleanup

```bash
sudo systemctl stop kimico-gateway
sudo systemctl disable kimico-gateway
tmux kill-session -t =kimico
```

Result:

```
inactive
disabled
# remaining tmux sessions: kimi02, qq-6, test (untouched)
# preserved: /etc/kimico-gateway, /var/lib/kimico-gateway, /opt/kimico-gateway
# /etc/kimico-gateway/config.toml: 600 ubuntu:ubuntu
# shotsmith-* containers still running, unchanged
```

Installed files and the fake kimico were deliberately kept for ticket 16.
Only the `kimico` session created by this verification was killed; no other
tmux session, service, or container was touched.

## 11. Summary

| Acceptance item | Result |
|---|---|
| Unit uses `KillMode=process`, `Restart=on-failure`, `RestartPreventExitStatus=2`, start limits | PASS (static + `systemctl show`) |
| `systemctl restart` leaves tmux/kimico pane PID unchanged | PASS (`2301971` before/after, `pane_dead=0`) |
| Config owned by service user, mode 600, readable | PASS (`600 ubuntu:ubuntu`) |
| HOME, TERM, PATH, loopback `no_proxy`, absolute kimico command | PASS (`systemctl show` + absolute command) |
| NapCat pinned image, host networking, loopback-only ports | PASS static only; runtime blocked by port 3000 conflict |
| OneBot array format, matching tokens, documented report URL | PASS static |
| Guide documents main/gateway QQ, risk, backup, upgrade, uninstall | PASS (`deploy/README.md`) |

Tests:

```
tests/test_deploy_artifacts.py: 18 passed
full suite:                     388 passed
```
