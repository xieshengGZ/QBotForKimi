# kimico QQ 远程控制网关 · PRD 套件

本目录是一套**自包含**的产品需求文档，是 AI 实现本项目的**唯一依据**。读者只需阅读本目录，无需访问任何外部文档或历史决策文档。

## 项目一句话

在同一台机器上，用手机主 QQ 私聊（经 NapCat / OneBot v11）远程查看并控制 tmux 会话中运行的 kimico 交互式进程；本地终端 attach 体验与直接运行 kimico 完全一致，两个入口共享同一个 kimico 进程。

## 文档地图

| 文件 | 内容 | 读者何时用 |
|---|---|---|
| `00-overview.md` | 目标/非目标、运行模型、术语表 | 首先阅读 |
| `01-architecture.md` | 进程/线程模型、模块依赖、数据流、共享状态 | 实现任何模块前必读 |
| `02-config.md` | config.toml 全字段定义、默认值、校验规则 | 实现 config 与任何读取配置的模块时 |
| `03-state.md` | state.json 结构、原子写入、读写时机 | 实现 state 时 |
| `10-mod-config.md` | config.py 模块规约 | 按构建顺序实现 |
| `11-mod-state.md` | state.py 模块规约 | 同上 |
| `12-mod-onebot.md` | onebot.py 模块规约（含内嵌 OneBot v11 协议） | 同上 |
| `13-mod-router.md` | router.py 模块规约（含全部固定文案） | 同上 |
| `14-mod-input.md` | input.py 模块规约（队列与 tmux 注入） | 同上 |
| `15-mod-capture.md` | capture.py 模块规约（差分/稳定判定，附伪代码） | 同上 |
| `16-mod-mirror.md` | mirror.py 模块规约（过滤/回显/切段/发送） | 同上 |
| `17-mod-session.md` | session.py 模块规约（tmux 生命周期与恢复） | 同上 |
| `18-mod-main.md` | __main__.py 模块规约（启动与线程编排） | 同上 |
| `20-acceptance.md` | 10 条验收标准 → 可执行检查步骤 | 全部实现完成后 |
| `30-deploy.md` | systemd 单元、NapCat Docker 部署、安装手册 | 交付部署物时 |
| `40-build-order.md` | 8 步构建顺序 + 每步完成定义 | 规划实现顺序时 |

## 如何交给 AI 开发

1. 让 AI 按 `40-build-order.md` 的顺序实现，每步完成其"完成定义"后再进入下一步。
2. 模块文档（`1x-mod-*.md`）固定 7 节：**职责 / 接口签名 / 行为规约 / 边界条件 / 错误处理 / 日志要求 / 测试清单**。
3. 接口签名节中列出的跨模块公开接口（类名、函数签名、异常类型）**不得更改**；模块内部实现自由。
4. 所有固定文案（回执、提示、告警）以 `13-mod-router.md` 的文案表为唯一来源，逐字使用，不得改写。
5. 本套件中的规约是冻结决策，实现者不得自行变更行为；发现规约矛盾或缺失时，停下来向人确认，不要自由发挥。

## 全局技术约束

- 目标环境：Ubuntu 24.04 / Python 3.12 / tmux 3.4。
- 仅使用 Python 标准库：`http.server`、`queue`、`threading`、`subprocess`、`urllib.request`、`tomllib`、`json`、`logging`、`signal`、`dataclasses`、`enum`、`re`、`time`、`os` 等。运行时零第三方依赖；pytest 仅作开发依赖。
- 交付物为一个 Python 包 `kimico_gateway/`，内含 `config.py`、`state.py`、`onebot.py`、`router.py`、`input.py`、`capture.py`、`mirror.py`、`session.py`、`__main__.py` 共 9 个模块，以及 systemd 单元文件与部署手册要求的附属文件。
- 运行入口：`python3 -m kimico_gateway [config_path]`。
