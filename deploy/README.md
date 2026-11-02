# kimico-gateway 部署指南

本目录是 `docs/prd/30-deploy.md` 的可执行部署物。网关以 systemd 服务运行，
NapCat 以 Docker 容器运行，所有端口只绑定回环地址。

```
tmux + kimico  ←→  kimico-gateway  ←→  NapCat (Docker)  ←→  QQ
                    127.0.0.1:8080       127.0.0.1:3000
```

## 1. 两个 QQ 账号

| 角色 | 在哪 | 用途 |
|---|---|---|
| **网关 QQ** | 机器上的 NapCat 容器登录 | 机器人账号，收发消息、上报事件 |
| **主 QQ** | 用户手机 | 私聊控制网关；配置里的 `auth.main_qq` |

二者必须是不同账号。**主 QQ 是控制端，网关 QQ 是被控端。**
不要把网关 QQ 拉群或公开；主 QQ 只用于私聊控制。

## 2. 风险与合规

NapCat 属于非官方 QQ 协议端，存在账号风控、临时封禁甚至永久封号的风险。
请用专用小号作为“网关 QQ”，不要用主账号。终端输出可能包含敏感信息，
mirror 默认关闭；日志默认不记录正文。

## 3. 前置条件

- Linux + systemd 255（或兼容版本）
- Python 3.12，本机为 `/usr/bin/python3.12`
- tmux ≥ 3.4（`tmux -V`）
- Docker 与 Docker Compose
- 一个专用的网关 QQ 号

## 4. 安装网关

```bash
cd /path/to/repo
sudo ./deploy/install.sh
```

脚本会：

1. 创建 `/etc/kimico-gateway`、`/var/lib/kimico-gateway`、`/opt/kimico-gateway`；
2. 复制 `kimico_gateway/` 到 `/opt/kimico-gateway/kimico_gateway`；
3. 首次安装时把 `deploy/config.toml.example` 复制为
   `/etc/kimico-gateway/config.toml`，属运行用户、权限 **600**；
4. 安装 `/etc/systemd/system/kimico-gateway.service`，执行
   `systemctl daemon-reload`，并 `systemctl enable --now kimico-gateway`。

已有 `config.toml` 不会被覆盖。重复执行安装是幂等的；只想刷新文件、
不启动服务时使用 `--no-start`：

```bash
sudo ./deploy/install.sh --no-start
```

安装后先编辑配置：

```bash
sudo editor /etc/kimico-gateway/config.toml
```

至少替换：

- `server.access_token`：NapCat 上报事件时使用的 token
- `onebot.access_token`：网关调用 NapCat HTTP API 时使用的 token
- `auth.main_qq`：主 QQ 号
- `tmux.kimico_command`：kimico 的绝对路径（例如
  `/home/ubuntu/.local/bin/kimico`）

然后启动或重启：

```bash
sudo systemctl restart kimico-gateway
sudo systemctl status kimico-gateway
journalctl -u kimico-gateway -f
```

### 4.1 配置权限

配置文件必须由服务用户拥有且为 600：

```bash
stat -c '%a %U:%G' /etc/kimico-gateway/config.toml
# 期望：600 ubuntu:ubuntu
sudo -u ubuntu test -r /etc/kimico-gateway/config.toml && echo "config readable"
```

安装脚本会强制 `ubuntu:ubuntu` 与 600；如果手工改过，重新跑
`sudo ./deploy/install.sh --no-start` 即可恢复。

### 4.2 服务环境：HOME、TERM、PATH、no_proxy

systemd unit 显式设置：

- `HOME=/home/ubuntu`：tmux 与 kimico 需要可写的用户环境；
- `TERM=xterm-256color`：tmux 需要可用的终端类型；
- `PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin`：
  让用户级安装的 kimico 可被找到；
- `no_proxy=127.0.0.1,localhost`：防止代理拦掉回环 API 与上报。

即使设置了 PATH，也建议在 `tmux.kimico_command` 写绝对路径；二者任一有效即可。
可用下面的命令确认服务实际拿到的环境：

```bash
systemctl show kimico-gateway -p Environment
sudo -u ubuntu bash -lc 'command -v kimico'
```

`kimico` 不在 PATH 时，`/opt/kimico-gateway` 的启动预检会以退出码 3 失败，
journal 会给出明确提示。

## 5. systemd 行为

unit 的关键设置：

| 设置 | 原因 |
|---|---|
| `KillMode=process` | `systemctl restart` 只杀网关主进程，tmux/kimico 继续存活 |
| `Restart=on-failure` | 崩溃或线程死亡后自动恢复 |
| `RestartPreventExitStatus=2` | 配置错误退出码 2 时不重启，避免刷 journal |
| `StartLimitIntervalSec=60` / `StartLimitBurst=5` | 限制短时间内反复重启 |
| `After=network-online.target docker.service` | 等网络与 Docker 就绪 |

`KillMode=process` 是 v1.1 的过渡方案；长期应把 tmux 生命周期拆到独立
unit 或登录会话，再恢复默认 `KillMode=control-group`。

验证重启不影响 kimico：

```bash
tmux display-message -p -t kimico '#{pid}'
sudo systemctl restart kimico-gateway
tmux display-message -p -t kimico '#{pid}'   # PID 必须不变
tmux display-message -p -t kimico '#{pane_dead}'  # 0
```

## 6. NapCat 部署

### 6.1 镜像

`deploy/napcat/compose.yaml` 固定镜像版本，不使用 `latest`：

```yaml
image: mlikiowa/napcat-docker:v4.8.0
```

**假设**：本仓库离线构建时无法访问 Docker Hub 核对 tag，`v4.8.0` 是
写入部署物的具体版本。首次联网部署前请用
`docker manifest inspect mlikiowa/napcat-docker:v4.8.0`（或 Docker Hub）
确认 tag 存在；也可把 image 改为
`mlikiowa/napcat-docker@sha256:<digest>` 固定 digest。

### 6.2 启动容器

最简单的方式：

```bash
cd /home/ubuntu/qq
sudo ./deploy/napcat/start.sh <网关QQ号>
sudo docker logs -f napcat     # 看二维码，扫码登录网关 QQ
```

`start.sh` 会自动：

1. 创建 `/opt/napcat/config` 和 `/opt/napcat/qq`；
2. 首次运行启动固定镜像的 `napcat` 容器；
3. 再次运行复用已有容器（未运行则 `docker start`），不会重复创建。

需要自定义时可用环境变量覆盖：

```bash
sudo NAPCAT_IMAGE=mlikiowa/napcat-docker:v4.8.0 \
     NAPCAT_CONFIG_DIR=/opt/napcat/config \
     ./deploy/napcat/start.sh <网关QQ号>
```

手动等价命令（不经过脚本）：

```bash
sudo mkdir -p /opt/napcat/config /opt/napcat/qq
sudo docker run -d --name napcat --restart unless-stopped \
  --network host \
  -v /opt/napcat/config:/app/napcat/config \
  -v /opt/napcat/qq:/app/.config/QQ \
  -e ACCOUNT=<网关QQ号> \
  mlikiowa/napcat-docker:v4.8.0
```

要点：

- `--network host` 是必需的，容器要直接访问宿主 `127.0.0.1:8080`
  并提供 `127.0.0.1:3000`；
- 配置卷 `/opt/napcat/config:/app/napcat/config` 持久化登录与 OneBot 配置；
- `restart: unless-stopped` 让容器随机器重启；
- 不要添加 `-p` 端口映射，也不要把 NapCat 的 HTTP 服务绑到 `0.0.0.0`。

### 6.3 登录网关 QQ

按 NapCat 文档**扫码登录**，二维码在容器日志里：

```bash
sudo docker logs -f napcat
```

扫码账号是“网关 QQ”，不是手机上的主 QQ。

### 6.4 OneBot v11 配置

把 `deploy/napcat/onebot11.template.json` 复制到 NapCat 配置目录并改名为
`onebot11_<网关QQ>.json`，替换两个 token 占位符：

| NapCat 项 | 值 | 对应网关配置 |
|---|---|---|
| HTTP 服务器地址/端口 | `127.0.0.1:3000` | `onebot.api_base = "http://127.0.0.1:3000"` |
| HTTP 服务器 Access Token | `REPLACE_WITH_ONEBOT_ACCESS_TOKEN` | `onebot.access_token` |
| HTTP POST 上报地址 | `http://127.0.0.1:8080/cq` | `server.path = "/cq"` |
| POST 上报 Access Token | `REPLACE_WITH_SERVER_ACCESS_TOKEN` | `server.access_token` |
| 消息格式 | `array` | 网关只解析数组消息段 |
| 上报类型 | 至少包含 message 事件 | — |

两侧 token 必须一致；网关以 Bearer token 鉴权。修改配置后重启容器：

```bash
sudo docker restart napcat
```

连通性自测：

```bash
curl -X POST http://127.0.0.1:3000/send_private_msg \
  -H "Authorization: Bearer <onebot.access_token>" \
  -H "Content-Type: application/json" \
  -d '{"user_id":<主QQ>,"message":"gateway link ok","auto_escape":true}'
```

然后用主 QQ 给网关 QQ 发 `#status`，应收到八行状态回执。

### 6.5 回环端口

| 端口 | 绑定 | 说明 |
|---|---|---|
| 8080 | `127.0.0.1` | 网关 HTTP 接入，NapCat 上报 |
| 3000 | `127.0.0.1` | NapCat OneBot HTTP API |

如果 NapCat 因配置变化监听了 `0.0.0.0`，必须用防火墙限制来源，或改回回环。

## 7. 备份

升级或改配置前备份：

```bash
sudo tar czf kimico-gateway-backup-$(date +%F).tar.gz \
  /etc/kimico-gateway/config.toml \
  /var/lib/kimico-gateway/state.json \
  /opt/napcat/config
```

`state.json` 只保存 mirror 开关；NapCat 配置含登录凭据，注意保管。

## 8. 升级

1. 先备份（见上一节）；
2. 拉取新代码，重新执行 `sudo ./deploy/install.sh`；
3. 配置与 state 保留，脚本会刷新 `/opt/kimico-gateway` 与 unit；
4. 如果服务已在运行，脚本会 `systemctl restart kimico-gateway`；
   tmux/kimico 不受影响（`KillMode=process`）；
5. NapCat 升级时显式修改 `compose.yaml` 的镜像 tag，先备份 `/opt/napcat/config`，
   再 `docker compose up -d`。

```bash
journalctl -u kimico-gateway --since today
tmux display-message -p -t kimico '#{pane_dead}'
```

## 9. 卸载

```bash
sudo ./deploy/uninstall.sh          # 停服务、删 unit 和 /opt/kimico-gateway
sudo ./deploy/uninstall.sh --purge  # 额外删除 /etc 与 /var/lib 下的数据
```

默认保留配置与 state，便于重装。脚本**不会**：

- 停止或删除 NapCat 容器（手工执行 `sudo docker rm -f napcat`）；
- 杀掉 tmux 会话（手工执行 `tmux kill-session -t kimico`，确认里面没有
  需要保留的 kimico 上下文）。

停服务但保留文件：

```bash
sudo systemctl disable --now kimico-gateway
```

## 10. 故障排查

1. `journalctl -u kimico-gateway -n 200` 看退出码与线程日志；
   退出码 2 = 配置错误，3 = kimico/tmux 预检失败，4 = 端口绑定失败，5 = 线程死亡。
2. `tmux has-session -t kimico` 与 `#{pane_dead}` 判断会话状态。
3. `sudo -u ubuntu test -r /etc/kimico-gateway/config.toml` 确认权限。
4. `sudo -u ubuntu bash -lc 'command -v kimico'` 确认 PATH。
5. `curl` NapCat API 确认出站；用主 QQ 发 `#status` 确认入站。
6. 若 `systemctl restart` 后 tmux 消失，检查 tmux server 是否确实由网关
   首次拉起，以及 unit 是否仍是 `KillMode=process`。

## 11. 部署物清单

| 文件 | 用途 |
|---|---|
| `deploy/kimico-gateway.service` | systemd unit |
| `deploy/config.toml.example` | 配置模板（token 为占位符） |
| `deploy/napcat/compose.yaml` | 固定版本 NapCat + host 网络 |
| `deploy/napcat/onebot11.template.json` | OneBot array/回环/上报模板 |
| `deploy/install.sh` | 幂等安装 |
| `deploy/uninstall.sh` | 卸载（默认保留配置/state） |
| `tests/test_deploy_artifacts.py` | 部署物静态校验 |
