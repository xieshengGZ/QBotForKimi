# kimico QQ Gateway

同一台机器上，用手机 QQ 私聊控制 tmux 中运行的 kimico 进程，并保持本地终端体验一致。

## Language

**kimico**:
被控制的 Kimi CLI 交互式进程。
_Avoid_: Kimi、CLI、被控进程

**网关**:
在同一台机器上连接 QQ 与 kimico 的 Python 进程，本项目交付物。
_Avoid_: gateway、bridge、proxy

**主 QQ**:
配置中指定的唯一有权控制网关的手机 QQ 号。
_Avoid_: 用户 QQ、控制 QQ、管理员 QQ

**网关 QQ / 机器人 QQ**:
NapCat 登录的机器端 QQ 号；它与主 QQ 是不同账号。
_Avoid_: 主 QQ、机器人账号（不带限定）

**mirror**:
控制 kimico 输出是否额外推送到手机 QQ 的开关。
_Avoid_: 镜像模式、同步、转发

**控制指令**:
以 `#` 开头且不是 `##` 的消息；只由网关处理，不注入 kimico。
_Avoid_: 命令、指令、bot 命令

**注入**:
把 QQ 消息文本送入 kimico 的输入并提交。
_Avoid_: 转发、发送、输入

**回显**:
kimico 终端对注入文本的显示。
_Avoid_: 回声、重复消息

**稳定行**:
连续若干轮轮询内容不变、可以进入推送管道的输出行。
_Avoid_: 完成行、最终行

**tmux 会话**:
tmux server 中承载 pane 的命名容器；本项目只使用一个。
_Avoid_: 会话（不带限定）、终端会话

**Kimi 会话**:
由 `/session` 切换的 Kimi 对话上下文。
_Avoid_: 会话（不带限定）、对话

**恢复**:
kimico 进程退出后按退避策略重新启动的统称；重建会丢失 kimico 内存上下文。
_Avoid_: 修复
