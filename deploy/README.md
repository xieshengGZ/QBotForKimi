# kimico 单服务部署

一个 systemd 服务 `kimico` 带起全部组件：网关、tmux 里的 kimi、以及 NapCat
Docker 容器。你只需要填两个 QQ 号，不需要配置端口，也不需要分别启动
NapCat 和网关。

```
手机 QQ（主 QQ）
      │ 私聊
      ▼
NapCat 容器（网关 QQ）  ←→  kimico 服务  ←→  tmux + kimi
       自动创建               systemd
```

## 1. 快速开始

前置条件：Linux + systemd、Python 3.12、tmux、Docker、一个专用的网关 QQ 号。

```bash
cd /path/to/repo
sudo ./deploy/install.sh
```

首次安装会生成 `/etc/kimico/config.toml`。只改这两个数字：

```toml
[qq]
gateway_qq = 123456789   # 网关 QQ：NapCat 登录的机器人小号
main_qq = 987654321      # 主 QQ：你手机上用来控制的账号
```

然后启动并扫码：

```bash
sudo systemctl restart kimico
sudo docker logs -f kimico-napcat    # 出现二维码后用网关 QQ 扫码
```

扫码完成后，用主 QQ 私聊网关 QQ 发 `#status`，应收到状态回执。

> `gateway_qq` 和 `main_qq` 必须是两个不同的账号。主 QQ 是控制端，
> 网关 QQ 是被控端，不要把网关 QQ 拉群或公开。

## 2. 你不需要配置端口和 token

配置文件里只有两个 QQ 号。服务启动时自动完成：

| 自动项 | 位置 / 行为 |
|---|---|
| 网关回环端口 | 优先 8080，被占用时自动换空闲端口 |
| NapCat OneBot 端口 | 优先 3000，被占用时自动换 13000+ |
| 两侧 access token | 自动生成并长期复用 |
| NapCat 的 OneBot 配置 | `/var/lib/kimico/napcat/config/onebot11_<网关QQ>.json` |
| 完整运行配置 | `/var/lib/kimico/config.toml`（生成文件，不要手改） |
| NapCat 容器 | `kimico-napcat`，镜像 `mlikiowa/napcat-docker:v4.8.124` |

端口只在 `127.0.0.1` 回环上使用，不对外开放。生成的状态和登录信息都在
`/var/lib/kimico`。

`v4.8.124` 是 Docker Hub 上真实存在的固定版本；旧的 `v4.8.0` 并不存在，
因此首次启动会拉取镜像。想换镜像时在配置里加：

```toml
[napcat]
image = "mlikiowa/napcat-docker:v4.8.124"
```

## 3. 服务管理

```bash
sudo systemctl start kimico      # 启动网关、kimi、NapCat
sudo systemctl stop kimico       # 全部停止（包括 NapCat 容器）
sudo systemctl restart kimico    # 改完配置后重启
sudo systemctl status kimico
journalctl -u kimico -f
```

服务是一个整体：`systemctl stop kimico` 会同时停掉 NapCat 容器；
`systemctl start kimico` 会把容器重新拉起。QQ 登录信息保存在
`/var/lib/kimico/napcat/qq`，重启后通常不需要重新扫码。

自动生成的端口和 token 会持久化在 `/var/lib/kimico/plan.json`，所以重启后
NapCat 和网关仍然对得上，不需要人工同步。

### kimi 命令

默认自动查找 `kimi` / `kimico`。找不到或想指定参数时，在配置里加：

```toml
[kimico]
command = "/home/ubuntu/.kimi-code/bin/kimi"
working_directory = "/home/ubuntu/qq_work"
```

`working_directory` 固定 kimi 的启动目录；缺省时继承服务目录
`/opt/kimico`。目录不存在时服务启动会自动创建，属主必须是运行用户。

## 4. 风险与合规

NapCat 属于非官方 QQ 协议端，存在账号风控、临时封禁甚至永久封号的风险。
请用专用小号作为“网关 QQ”，不要用主账号。终端输出可能包含敏感信息，
mirror 默认关闭；日志默认不记录正文。

## 5. 故障排查

1. `systemctl status kimico` 和 `journalctl -u kimico -n 200` 看启动阶段错误。
   - 退出码 2：`/etc/kimico/config.toml` 两个 QQ 号缺失、相同或仍是模板值。
   - 退出码 3：Docker/镜像/容器问题；journal 会给出具体 `docker` 错误。
2. `sudo docker ps -a --filter name=kimico-napcat` 确认容器存在。
3. `sudo docker logs -f kimico-napcat` 看二维码和 NapCat 日志。
4. 确认服务用户能访问 Docker：unit 已设置 `SupplementaryGroups=docker`；
   若手工改过 unit，保留这一行。
5. 用主 QQ 发 `#status`；无回执时先确认 NapCat 已登录，再看
   `/var/lib/kimico/config.toml` 里的 `onebot.api_base` 端口是否与
   NapCat 配置一致（正常情况下由服务自动维护）。

## 6. 备份、升级、卸载

备份：

```bash
sudo tar czf kimico-backup-$(date +%F).tar.gz \
  /etc/kimico/config.toml \
  /var/lib/kimico
```

升级：

```bash
cd /path/to/repo
git pull
sudo ./deploy/install.sh      # 保留配置、状态和 NapCat 登录
```

卸载：

```bash
sudo ./deploy/uninstall.sh           # 停服务、删代码和容器，保留配置/登录
sudo ./deploy/uninstall.sh --purge   # 连 /etc/kimico 与 /var/lib/kimico 一起删
```

`--purge` 会删除 NapCat 登录数据，下次需要重新扫码。

## 7. 文件清单

| 文件 | 用途 |
|---|---|
| `deploy/kimico.service` | 唯一的 systemd unit |
| `deploy/config.toml.example` | 只含两个 QQ 号的配置模板 |
| `deploy/napcat/onebot11.template.json` | NapCat OneBot 模板，由服务自动填充 |
| `deploy/install.sh` | 幂等安装；自动停用旧的 `kimico-gateway` unit |
| `deploy/uninstall.sh` | 卸载（默认保留配置/状态/登录） |
| `kimico_gateway/bootstrap.py` | 生成端口/token/配置并管理 NapCat 容器 |
