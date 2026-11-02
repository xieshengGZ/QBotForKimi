# 40 · 构建顺序与完成定义

按依赖关系排序的 8 步实现计划。每步完成其"完成定义"后再进入下一步；除第 8 步外，所有验证均可在无真实 QQ 的环境完成。

## Step 1：config / state / logging 地基

- 实现：`config.py`（按 `10-mod-config.md`）、`state.py`（按 `11-mod-state.md`）、日志初始化片段（格式见 `18-mod-main.md` 第 4.3 节，可先放在 `__main__.py` 草稿中）。
- 完成定义：
  - `10-mod-config.md` 与 `11-mod-state.md` 测试清单全部通过；
  - `python3 -m kimico_gateway bad.toml` 以退出码 2 失败并打印逐行错误。

## Step 2：session —— tmux 创建、检测、respawn

- 实现：`session.py` 中 `session_exists / ensure_session / is_pane_dead / respawn_pane`（`17-mod-session.md` 第 2–3 节；`RecoveryManager` 留到 Step 7）。
- 完成定义：
  - 第 8 节测试清单中 ensure/is_pane_dead/respawn 相关用例通过；
  - 手工：真机执行 ensure_session 创建会话（kimico_command 先用 `bash` 验证），`tmux attach` 可见；kill 进程后 `is_pane_dead` 返回 True，`respawn_pane` 后恢复。

## Step 3：input —— 队列、文本注入

- 实现：`input.py` 的 `InboundQueue` 与 `inject`（`14-mod-input.md` 第 2–3 节；worker 循环依赖 router，可暂以最小桩替代）。
- 完成定义：
  - 队列与注入相关测试通过；
  - 手工：向 Step 2 的 `bash` pane 注入 `echo hi`（含多行文本），pane 中原样出现并执行；注入失败（先 kill tmux server 再注入）抛 `InjectError`。

## Step 4：capture / mirror —— 输出管道

- 实现：`capture.py` 全部（`15-mod-capture.md`）、`mirror.py` 全部（`16-mod-mirror.md`）。
- 完成定义：
  - 两个模块测试清单全部通过（差分、稳定判定、过滤、回显、切段、队列、发送循环）；
  - 手工集成：真 tmux 会话跑 `bash`，监控循环把 `seq 1 100` 的输出切成多段送入假发送函数，段长 ≤ 1500、顺序正确、尾行延迟生效（连续输出时最后一行在下轮才出）。

## Step 5：onebot —— HTTP 接入与发送

- 实现：`onebot.py` 全部（`12-mod-onebot.md`）。
- 完成定义：
  - 测试清单全部通过；
  - 手工：启动 `OnebotServer`，用 `20-acceptance.md` 0.3 的 curl 模板发送事件，合法事件触发回调、各类非法输入返回对应状态码；`send_private_msg` 对假接收器（0.2）成功投递。

## Step 6：router —— 指令解析与回执

- 实现：`router.py` 全部（`13-mod-router.md`）；把 `run_input_worker` 从桩替换为完整分支逻辑（`14-mod-input.md` 第 4 节）。
- 完成定义：
  - router 与 input worker 测试清单全部通过；
  - 手工链路：curl 发 `#mirror on`、`#status`、未知指令，假接收器收到与文案表逐字一致的回执；普通消息注入 tmux 且回显被抵消。

## Step 7：recovery —— 退避重启与告警 + __main__ 编排

- 实现：`RecoveryManager`（`17-mod-session.md` 第 4 节）、`__main__.py` 全部（`18-mod-main.md`）。
- 完成定义：
  - RecoveryManager 与 __main__ 测试清单全部通过；
  - 整机冒烟：完整启动网关（接假 NapCat 出站），`pkill` 假 kimico，按 backoff 自动重启并收到通知；秒退命令触发 3 次失败后熔断告警；
  - `systemctl restart` 语义验证：kill -TERM 网关进程，退出码 0、state 持久化、tmux/kimico 存活。

## Step 8：测试补齐、systemd 单元、部署手册、全量验收

- 实现：整理 pytest 工程（`tests/`，`pytest` 全绿）；产出部署物（`30-deploy.md` 的 systemd 单元与配置模板）；执行 `20-acceptance.md`。
- 完成定义：
  - `pytest` 全部通过；
  - 按 `30-deploy.md` 在一台干净 Ubuntu 24.04 完成部署，systemd 服务正常运行；
  - `20-acceptance.md` 验收 1–10 全部通过（假环境），真机联调条目（第 2 节）通过。

## 依赖关系速查

```
Step 1 (config/state)
  ├─▶ Step 2 (session 基础)
  │     └─▶ Step 7 (RecoveryManager, __main__)
  ├─▶ Step 3 (input 基础) ──────────────┐
  ├─▶ Step 4 (capture/mirror) ──┐       │
  ├─▶ Step 5 (onebot) ──────────┼───────┤
  └─▶ Step 6 (router + worker) ─┴───────┴─▶ Step 7 ─▶ Step 8
```
