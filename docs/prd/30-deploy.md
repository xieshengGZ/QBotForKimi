# 30 · 部署与运维

## 1. 组件拓扑与端口

| 组件 | 部署方式 | 监听 | 说明 |
|---|---|---|---|
| kimico-gateway | systemd 服务（本机用户或专用用户） | `127.0.0.1:8080`（HTTP 接入，仅回环） | Python 3.12，零第三方依赖 |
| NapCat | Docker（`--network host`） | `127.0.0.1:3000`（OneBot HTTP API） | 事件上报到 `http://127.0.0.1:8080/cq` |
| tmux + kimico | 由网关创建/接管 | — | 独立于网关进程存活 |

所有端口仅绑定回环；如因 NapCat 配置变化导致端口暴露，须在防火墙限制来源。

## 2. 网关安装

### 2.1 目录与文件

```
/etc/kimico-gateway/config.toml      # 配置（600，属运行用户）
/var/lib/kimico-gateway/state.json   # 运行期状态（自动创建）
/opt/kimico-gateway/kimico_gateway/  # 代码包（9 个模块）
/etc/systemd/system/kimico-gateway.service
```

### 2.2 安装步骤

```bash
sudo mkdir -p /etc/kimico-gateway /var/lib/kimico-gateway /opt/kimico-gateway
sudo cp -r kimico_gateway /opt/kimico-gateway/
sudo install -o ubuntu -g ubuntu -m 600 \
  config.toml /etc/kimico-gateway/config.toml
sudo chown -R ubuntu:ubuntu /var/lib/kimico-gateway
```

前置：`sudo apt install -y python3.12 tmux docker.io`（tmux ≥ 3.4；`tmux -V` 确认）。

安装后自检：

```bash
sudo -u ubuntu test -r /etc/kimico-gateway/config.toml && echo "config readable"
sudo -u ubuntu bash -lc 'command -v kimico' || echo "kimico not in service PATH"
```

`kimico` 如果是 nvm/pyenv 等用户环境安装，必须在 `config.toml` 的 `kimico_command` 写绝对路径，或在 unit 中配置正确的 `PATH`。

### 2.3 systemd 单元

`/etc/systemd/system/kimico-gateway.service`：

```ini
[Unit]
Description=kimico QQ remote control gateway
After=network-online.target docker.service
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/kimico-gateway
Environment=HOME=/home/ubuntu
Environment=TERM=xterm-256color
Environment=PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
Environment=no_proxy=127.0.0.1,localhost
ExecStart=/usr/bin/python3.12 -m kimico_gateway /etc/kimico-gateway/config.toml
Restart=on-failure
RestartSec=3
RestartPreventExitStatus=2
KillMode=process
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kimico-gateway
journalctl -u kimico-gateway -f
```

### 2.4 为什么是 KillMode=process

systemd 默认 `KillMode=control-group`：`systemctl restart` 会杀掉 unit cgroup 内所有进程。若 tmux server 是网关首次拉起的，它就在该 cgroup 内，kimico 会一起被杀，违反“网关重启不影响 kimico”。

v1.1 采用 `KillMode=process`：只杀网关主进程，tmux server/kimico 留在 cgroup 中继续存活。这是**过渡方案**（systemd 官方不推荐长期使用），必须用验收 9 验证。

长期方案（v1.2 候选）：把 tmux server 的生命周期从网关 unit 拆出去——由用户 login session 或独立的 `kimico-tmux.service` 先启动 tmux server；网关只做 `ensure_session` 接管。这样网关可以恢复 `KillMode=control-group`。

## 3. NapCat 部署

### 3.1 运行容器

固定镜像版本或 digest，不要用 `latest`：

```bash
sudo docker run -d \
  --name napcat \
  --restart unless-stopped \
  --network host \
  -v /opt/napcat/config:/app/napcat/config \
  -e ACCOUNT=<网关QQ号> \
  mlikiowa/napcat-docker:<PINNED_VERSION>
```

`--network host` 为**必需**：容器内需直接访问宿主 `127.0.0.1:8080`（上报）与开放 `127.0.0.1:3000`（API）。确认 NapCat 的 HTTP 服务只绑定 `127.0.0.1`，不要在 `0.0.0.0` 暴露。

### 3.2 登录 QQ

按 NapCat 文档完成扫码登录（查看容器日志获取二维码：`docker logs -f napcat`），登录账号为“网关 QQ”（机器端账号）。**主 QQ 是用户手机上的账号，二者不同**。

### 3.3 OneBot v11 配置

在 NapCat 的 OneBot 配置中：

| 配置项 | 值 |
|---|---|
| 启用 HTTP 服务器 | 开 |
| HTTP 监听地址/端口 | `127.0.0.1:3000` |
| HTTP Access Token | 与网关 `onebot.access_token` 一致 |
| 启用 HTTP POST 上报 | 开 |
| 上报地址 | `http://127.0.0.1:8080/cq` |
| 上报 Access Token | 与网关 `server.access_token` 一致 |
| 消息格式 | **array**（数组消息段；网关不解析 string 格式） |
| 上报类型 | 至少包含消息事件（message） |

### 3.4 联通自测

```bash
# 出站方向：直接调 NapCat API 给主 QQ 发消息
curl -X POST http://127.0.0.1:3000/send_private_msg \
  -H "Authorization: Bearer <onebot.access_token>" \
  -H "Content-Type: application/json" \
  -d '{"user_id":<主QQ>,"message":"gateway link ok","auto_escape":true}'

# 入站方向：用主 QQ 给网关 QQ 私聊发 #status，应收到状态回执
```

## 4. 日常运维

| 操作 | 命令 |
|---|---|
| 查日志 | `journalctl -u kimico-gateway --since today` |
| 重启网关 | `sudo systemctl restart kimico-gateway`（v1.1 下 tmux/kimico 不受影响，验收 9 验证） |
| 查看 tmux | `tmux attach -t kimico`（退出用 `Ctrl-b d`，勿 Ctrl-C kimico 之外误杀） |
| 手工恢复 kimico | 在网关熔断时先排查原因，再 `sudo systemctl restart kimico-gateway` 清零重试 |
| 重置 mirror | 改 `/var/lib/kimico-gateway/state.json` 后重启网关，或直接发 `#mirror on/off` |
| NapCat 日志 | `docker logs -f napcat` |
| 升级网关 | 替换 `/opt/kimico-gateway/kimico_gateway/` 后 `systemctl restart`；配置与 state 保留 |
| 检查 kimico 存活 | `tmux display-message -p -t kimico '#{pane_dead}'`（0 活，1 死） |

### 4.1 故障排查顺序

1. `journalctl -u kimico-gateway -n 200` 看退出码/线程死亡/恢复日志。
2. `tmux has-session -t kimico` 与 `#{pane_dead}` 判断会话/pane 状态。
3. `sudo -u ubuntu test -r /etc/kimico-gateway/config.toml` 确认配置权限。
4. `sudo -u ubuntu bash -lc 'command -v kimico'` 确认命令在服务 PATH 中。
5. `curl` NapCat API 确认出站；用 `curl` 伪造事件确认入站。
6. 若 `KillMode=process` 下 `systemctl restart` 仍杀掉 tmux，说明 tmux server 不在预期 cgroup/会话中，检查是否由网关首次拉起。

## 5. 风险与合规

- 使用 NapCat/非官方 QQ 协议端存在账号风控、临时封禁或永久封禁风险；建议使用专用小号作为“网关 QQ”，不要用主账号。
- 主 QQ 只用于私聊控制；不要把网关 QQ 拉群或公开。
- 终端输出可能包含敏感信息；mirror 默认关闭，日志默认不记录正文，发送失败只记长度/原因。
- 定期备份 `/etc/kimico-gateway/config.toml`、`/var/lib/kimico-gateway/state.json` 与 NapCat 配置目录；升级前先备份。

## 6. 卸载

```bash
sudo systemctl disable --now kimico-gateway
sudo docker rm -f napcat
tmux kill-session -t kimico 2>/dev/null || true
sudo rm -rf /etc/kimico-gateway /var/lib/kimico-gateway /opt/kimico-gateway /etc/systemd/system/kimico-gateway.service
sudo systemctl daemon-reload
```
