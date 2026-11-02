# QBotForKimi

用 QQ 私聊远程控制终端里的 Kimi Code CLI。

手机上的**主 QQ** 给机器人的**网关 QQ** 发消息，消息会被注入到 tmux 里的
kimi；kimi 的终端输出可以再推送回你的 QQ。整个程序只安装一个 systemd
服务 `kimico`，它会自动拉起并管理 NapCat Docker 容器。

```
手机 QQ（主 QQ）
      │ 私聊
      ▼
NapCat 容器（网关 QQ）  ←→  kimico 服务  ←→  tmux + kimi
       自动创建               systemd
```

## 功能

- QQ 私聊直接向 kimi 注入文本
- `#status`、`#mirror on/off`、`#clear` 控制指令
- kimi 终端输出镜像回 QQ（默认关闭）
- NapCat 容器、端口、access token、OneBot 配置全部自动生成
- 一个 systemd 服务管理网关、kimi 和 NapCat
- 崩溃自动重启，tmux/kimi 跨网关重启保留

## 前置条件

- Linux + systemd
- Python 3.12（默认 `/usr/bin/python3.12`）
- tmux
- Docker（当前用户为 `ubuntu` 时，服务通过 `SupplementaryGroups=docker`
  访问 Docker）
- Git
- 已安装并登录过 Kimi Code CLI；服务用户需要能执行 `kimi`

默认服务用户是 `ubuntu`，安装脚本会把 unit 里的用户和家目录替换成实际值。
如果服务器用户不同：

```bash
sudo KIMICO_USER=你的用户名 ./deploy/install.sh
```

以 Ubuntu 为例：

```bash
sudo apt update
sudo apt install -y python3.12 tmux docker.io git
```

安装 Kimi Code CLI 后，先用服务用户运行一次并完成登录：

```bash
sudo -u ubuntu kimi
```

## 快速开始

### 1. 获取代码

首次安装：

```bash
git clone https://github.com/xieshengGZ/QBotForKimi.git
cd QBotForKimi
```

已经克隆过：

```bash
cd QBotForKimi
git pull
```

### 2. 安装服务

```bash
sudo ./deploy/install.sh
```

首次执行会：

- 安装代码到 `/opt/kimico`
- 生成配置模板 `/etc/kimico/config.toml`
- 安装并启用 `kimico.service`
- 配置还是模板占位值时**不会启动服务**

### 3. 只改两个 QQ 号

```bash
sudo editor /etc/kimico/config.toml
```

最小配置：

```toml
[qq]
# 网关 QQ：机器上的 NapCat 登录的机器人小号
gateway_qq = 123456789
# 主 QQ：你手机上的账号，用来私聊控制
main_qq = 987654321
```

两个号码必须不同。**主 QQ 是控制端，网关 QQ 是被控端。**

可选配置（推荐）：

```toml
[kimico]
# --auto = Never Ask 模式：工具调用和命令自动执行
command = "/home/ubuntu/.kimi-code/bin/kimi --auto"
# kimi 的固定工作目录；不存在会自动创建
working_directory = "/home/ubuntu/qq_work"
```

`command` 不填时会自动查找 `kimi` / `kimico`。`working_directory` 不填时
沿用服务的工作目录 `/opt/kimico`。

> `--auto` 会让 kimi 自动执行工具调用和命令，只应在你信任的机器上使用。

### 4. 启动

```bash
sudo systemctl start kimico
```

首次启动会自动拉取 NapCat 镜像
`mlikiowa/napcat-docker:v4.8.124`，可能需要几分钟。

### 5. 扫码登录网关 QQ

```bash
sudo docker logs -f kimico-napcat
```

用**网关 QQ** 扫描日志里的二维码。登录成功后，用**主 QQ** 私聊网关 QQ
发送：

```text
#status
```

收到状态回执即表示链路打通。

## 指令

| 发送内容 | 效果 |
|---|---|
| `#status` | 查看 tmux/kimi、mirror、队列等状态 |
| `#mirror on` | 开启 kimi 输出推送 |
| `#mirror off` | 关闭输出推送，并清空待发送内容 |
| `#clear` | 清空待发送输出并重置抓屏基线 |
| 普通文本 | 原样注入 kimi |
| `##文本` | 注入字面量 `#文本` |

**mirror 默认关闭**：普通文本仍会进入 kimi，但 kimi 的回答不会自动发回
QQ。需要接收回答时先发 `#mirror on`。

## 服务管理

```bash
sudo systemctl start kimico      # 启动网关、kimi、NapCat
sudo systemctl stop kimico       # 全部停止（包括 NapCat 容器）
sudo systemctl restart kimico    # 改完配置后重启
sudo systemctl status kimico
journalctl -u kimico -f
```

服务是一个整体：停止 `kimico` 会同时停止 NapCat；启动会重新拉起。
QQ 登录信息保存在 `/var/lib/kimico/napcat/qq`，正常重启通常不需要重新扫码。

## 自动生成的配置

你不需要手工配置端口或 token。

| 内容 | 位置 / 行为 |
|---|---|
| 完整运行配置 | `/var/lib/kimico/config.toml`（自动生成，不要手改） |
| 端口与 token | `/var/lib/kimico/plan.json`（自动复用） |
| NapCat OneBot 配置 | `/var/lib/kimico/napcat/config/onebot11_<网关QQ>.json` |
| NapCat 登录数据 | `/var/lib/kimico/napcat/qq` |
| 网关 HTTP 接入 | `127.0.0.1:8080`（被占用时自动换端口） |
| NapCat OneBot API | `127.0.0.1:13000+`（自动选择） |
| NapCat WebUI | `127.0.0.1:6099` |

所有服务端口只绑定回环地址，不对外开放。

## 更新

```bash
cd QBotForKimi
git pull
sudo ./deploy/install.sh
```

已有配置、状态和 NapCat 登录都会保留。安装脚本会刷新代码、unit，并在
服务已运行时重启它。

## 卸载

```bash
sudo ./deploy/uninstall.sh           # 停服务、删代码和容器，保留配置/登录
sudo ./deploy/uninstall.sh --purge   # 连 /etc/kimico 与 /var/lib/kimico 一起删
```

`--purge` 会删除 NapCat 登录数据，下次需要重新扫码。

## 故障排查

1. 先看服务日志：

   ```bash
   sudo systemctl status kimico
   journalctl -u kimico -n 200 --no-pager
   ```

   - 退出码 2：两个 QQ 号缺失、相同，或仍是模板占位值。
   - 退出码 3：Docker、镜像或容器问题，journal 会给出具体错误。

2. 看 NapCat：

   ```bash
   sudo docker ps -a --filter name=kimico-napcat
   sudo docker logs -f kimico-napcat
   ```

3. 没有回执：
   - 确认 NapCat 已用网关 QQ 登录
   - 确认你是用主 QQ 私聊网关 QQ，而不是群聊
   - 发 `#status` 看网关是否在线
   - 想收到 kimi 输出，先发 `#mirror on`

4. 不要手工修改 `/var/lib/kimico/config.toml` 或 NapCat 的 OneBot 配置；
   它们由服务在启动时生成。要改启动参数，改 `/etc/kimico/config.toml`。

5. 如果 NapCat 镜像拉取失败，确认机器能访问 Docker Hub，或给 Docker
   配置镜像加速器后重试：

   ```bash
   sudo systemctl restart kimico
   ```

## 安全与合规

- NapCat 属于非官方 QQ 协议端，存在账号风控、临时封禁甚至永久封号风险。
  请使用专用小号作为网关 QQ，不要使用主账号。
- 不要把网关 QQ 拉群或公开。
- 不要把 8080、6099、13000 等端口暴露到公网。
- `/etc/kimico/config.toml`、`/var/lib/kimico/plan.json` 和 NapCat 配置
  包含凭据，注意备份与权限。
- `kimi --auto` 会自动执行操作，请只在可信环境中使用。

## 目录

```text
deploy/                 systemd unit、安装/卸载脚本、配置模板
kimico_gateway/         网关代码（OneBot 接入、tmux、抓屏、镜像）
quickstart/             单文件精简版桥接器（可选）
tests/                  pytest 测试
docs/                   设计与运维文档
```

## 开发

```bash
python3.12 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

测试不需要真实 QQ 或 Docker。

## 相关文档

- 部署细节：`deploy/README.md`
- 领域词汇：`GLOSSARY.md`
- 设计文档：`docs/prd/`
