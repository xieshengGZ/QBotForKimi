# 00 · 总览

## 1. 目标

- 在电脑终端之外，用手机主 QQ 私聊控制同一台机器上的 kimico 交互式进程。
- 两个入口共享同一个 kimico 进程与内存上下文：电脑操作一半，手机可接续。
- 本地终端体验与直接运行 kimico 完全一致。
- 长任务在 tmux 后台持续运行，不受终端关闭、QQ 离线影响。

## 2. 非目标

- 不修改 kimico 源码，不改其原生 IO。
- 不做多会话池；Kimi 侧会话切换由用户发送 `/session`，网关原样转发、不解析。
- 仅文本私聊；不支持群聊、图片、文件、语音。
- 不做云端会话、Web 管理台、跨机调度。
- `#code`（调用 Kimi Code API）为 P2，不进入本期。
- 本网关不是 Kimi Code Skill。

## 3. 运行模型

- 单机、单实例、单 tmux 会话，会话名固定由配置给出（默认 `kimico`）；会话内只跑一个 kimico 进程。
- kimico 直接作为 pane 的启动命令运行，不经过交互式 shell。
- tmux 设置 `remain-on-exit on`：kimico 退出后 pane 保留为 dead 状态，便于检测与原地重启。
- 两个输入入口：本地终端 attach（由 tmux 直接接管）；手机 QQ 私聊经 NapCat → 网关。**网关是唯一向 kimico 注入输入的组件。**
- 输出固定渲染到本地终端；mirror=on 时网关额外把稳定新增文本推送到 QQ。
- 长任务在 tmux 后台持续运行；网关进程崩溃不影响 tmux server 与 kimico 存活。

## 4. 组件拓扑

```
手机 QQ 私聊
    │  (QQ 协议)
    ▼
NapCat（Docker，--network host）
    │  OneBot v11 HTTP POST 事件上报        ▲ OneBot v11 HTTP API（send_private_msg）
    ▼                                        │
网关进程 kimico_gateway（Python 3.12 单进程多线程）
    │  tmux set-buffer / paste-buffer / send-keys（注入）
    │  tmux capture-pane（输出轮询）
    ▼
tmux server ── 会话 kimico ── pane 运行 kimico 进程
    ▲
本地终端 tmux attach（直连，不经过网关）
```

## 5. 术语表

| 术语 | 定义 |
|---|---|
| kimico | 被控制的 Kimi CLI 交互式进程，运行在 tmux pane 中 |
| 网关 / 本网关 | Python 进程 kimico_gateway，本项目的交付物 |
| NapCat | QQ 协议端（无头 NTQQ），Docker 部署，提供 OneBot v11 接口 |
| OneBot v11 | QQ 机器人接口标准；事件用 HTTP POST 上报，发消息用 HTTP API |
| 主 QQ | 配置 `auth.main_qq` 指定的唯一有权控制网关的 QQ 号 |
| mirror | 手机端输出推送开关；on 时把稳定新增文本推送到 QQ |
| 控制指令 | 以 `#` 开头的消息，仅网关处理，不转发给 kimico |
| 注入 | 网关通过 tmux buffer 把 QQ 消息文本送入 kimico 所在 pane 并回车 |
| 基线 / 快照 | capture-pane 最近一次抓取的行列表；差分以它为基准 |
| 新增行 | 本次快照相对上次快照、重叠区之后出现的行 |
| 稳定行 | 连续若干轮轮询内容不变、允许推送到 QQ 的新增行 |
| 候选块 | 尚未达到稳定条件、仍在观察中的新增行集合 |
| 回显 | 注入文本在终端中的原样显示，需要从推送内容中滤除 |
| pane_dead | tmux 报告 pane 内进程已退出（remain-on-exit 下 pane 保留） |
| 恢复 | pane dead 后按退避策略 respawn kimico 的过程 |

## 6. 全局行为原则

- **镜像语义**：mirror 仅控制 QQ 端能否收到 kimico 输出；控制指令回执始终发送，不受 mirror 影响；本地终端显示不受 mirror 影响。
- **输入语义**：注入级串行，不做回合检测；连续消息按到达顺序依次注入。同一时刻只由一方输入，由操作人员保证，网关不检测本地输入状态。
- **不解析原则**：除 `#` 控制指令外，网关对所有消息（包括 `/session`）不解析、不改写，原样注入。
- **单实例原则**：不做任何多实例、多会话、多用户扩展。
