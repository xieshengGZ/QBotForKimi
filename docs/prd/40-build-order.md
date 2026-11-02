# 40 · 构建顺序与完成定义

按依赖关系排序的实现计划：Step 0 是真实 TUI 门禁，Step 1–8 是编码。每步完成其“完成定义”后再进入下一步；除 Step 0/8 外，所有验证均可在无真实 QQ 的环境完成。

## Step 0：真实 kimico TUI 门禁（先于编码）

- 目标：验证 `capture-pane + 行差分 + 稳定判定` 在真实 kimico 上是否可行，避免用 fake-kimico 的通过结果替代真机结论。
- 做法：
  1. 在 tmux 中运行真实 kimico，持续 5–10 分钟，交替本地输入与长回答。
  2. 每 300ms 执行 `capture-pane -p -J -S -5000`，记录 `find_overlap` 的 k 分布、`k=0` 比例、末行变化频率。
  3. 用 `mask_tail(1/2)` 与 `recover_new_lines` 的离线实现各跑一遍，统计恢复率。
  4. 验证 `paste-buffer -p -r` 的多行粘贴是否被 kimico 正确接收、是否多次提交。
- 完成定义（满足任一）：
  - **通过**：`k=0` 比例 < 10%，回答行丢失为 0，p95 延迟 ≤ 2s；进入 Step 1，按 v1.1 轮询方案实现。
  - **有条件通过**：`k=0` 比例高但 `recover_new_lines` 能恢复 ≥ 95% 的回答行；继续，但把 capture 做成可替换适配器，并在 Step 4 补 spike 回归。
  - **不通过**：回答行持续丢失；停止编码，改为评估 `tmux pipe-pane` + ANSI 解析方案，回到 PRD 重新冻结。
- 产物：`docs/output/spike/kimico-capture.md`（命令、样本、统计、结论）。没有该产物，不得进入 Step 1。

## Step 1：config / state / logging 地基

- 实现：`config.py`（按 `10-mod-config.md`）、`state.py`（按 `11-mod-state.md`）、日志初始化片段（格式见 `18-mod-main.md` 第 4.3 节，可先放在 `__main__.py` 草稿中）。
- 完成定义：
  - `10-mod-config.md` 与 `11-mod-state.md` 测试清单全部通过；
  - `python3 -m kimico_gateway bad.toml` 以退出码 2 失败并打印逐行错误；
  - `access_token = "change-me"` 被拒绝；
  - state 并发写测试通过，无临时文件残留。

## Step 2：session —— tmux 创建、四态检测、respawn

- 实现：`session.py` 中 `session_exists / ensure_session / get_pane_state / respawn_pane`（`17-mod-session.md` 第 2–3 节；`RecoveryManager` 留到 Step 7）。
- 完成定义：
  - 第 8 节测试清单中 ensure/get_pane_state/respawn 相关用例通过；
  - 手工：真机执行 ensure_session 创建会话（kimico_command 先用 `bash` 验证），`tmux attach` 可见；`set-option -g history-limit` 在 `new-session` 之前执行；
  - 接管已存在会话时 `remain-on-exit` 被设置为 on；
  - kill 进程后 `get_pane_state` 返回 DEAD，`respawn_pane` 后恢复；tmux 命令超时返回 UNKNOWN，不返回 DEAD。

## Step 3：input —— 队列、文本注入

- 实现：`input.py` 的 `InboundQueue` 与 `inject`（`14-mod-input.md` 第 2–3 节；worker 循环依赖 router，可暂以最小桩替代）。
- 完成定义：
  - 队列、命名 buffer、`-r`、超时、NUL 拒绝相关测试通过；
  - 手工：向 Step 2 的 `bash` pane 注入 `echo hi`（含多行文本），pane 中原样出现并执行；注入失败（先 kill tmux server 再注入）抛 `InjectError`，不裸抛 `TimeoutExpired`；
  - 用户默认 tmux 粘贴 buffer 不被覆盖。

## Step 4：capture / mirror —— 输出管道

- 实现：`capture.py` 全部（`15-mod-capture.md`）、`mirror.py` 全部（`16-mod-mirror.md`）。
- 完成定义：
  - 两个模块测试清单全部通过（KMP 差分、mask_tail、recover_new_lines、稳定判定、行级回显、批处理、双 lane、generation、发送循环）；
  - `find_overlap` 5000 行重复内容基准在 50ms 内（本机允许 5x 余量）；
  - 手工集成：真 tmux 会话跑 `bash`，监控循环把 `seq 1 3000` 的输出切成多段送入假发送函数，段长 ≤ 1500、顺序正确、尾行延迟生效；
  - 真实 kimico 的 Step 0 回归：`k=0` 比例、回答行丢失、p95 延迟满足 Step 0 的通过标准；若未满足，停止并回到架构选择。

## Step 5：onebot —— HTTP 接入与发送

- 实现：`onebot.py` 全部（`12-mod-onebot.md`）。
- 完成定义：
  - 测试清单全部通过（单线程顺序、401、负 Content-Length、413 close、message 非 list、proxy bypass）；
  - 手工：启动 `OnebotServer`，用 `20-acceptance.md` 0.3 的脚本发送事件，合法事件触发回调、各类非法输入返回对应状态码；`send_private_msg` 对假接收器（0.2）成功投递。

## Step 6：router —— 指令解析与回执

- 实现：`router.py` 全部（`13-mod-router.md`）；把 `run_input_worker` 从桩替换为完整分支逻辑（`14-mod-input.md` 第 4 节）。
- 完成定义：
  - router 与 input worker 测试清单全部通过；
  - `#status` 为 8 行，控制/输出队列字段正确；
  - 手工链路：curl 发 `#mirror on`、`#status`、未知指令、`##hello`，假接收器收到与文案表逐字一致的回执；普通消息注入 tmux 且回显被行级抵消。

## Step 7：recovery —— 退避重启与告警 + __main__ 编排

- 实现：`RecoveryManager`（`17-mod-session.md` 第 4 节）、`__main__.py` 全部（`18-mod-main.md`）。
- 完成定义：
  - RecoveryManager 与 __main__ 测试清单全部通过；
  - 启动即 DEAD 第一次等 5s；respawn 失败只计 1 次；UNKNOWN 不触发 respawn；MISSING 触发 recreate；
  - 整机冒烟：完整启动网关（接假 NapCat 出站），`pkill` 假 kimico，按 backoff 自动重启并收到通知；秒退命令触发 3 次失败后熔断告警；
  - `systemctl restart` 语义验证：kill -TERM 网关进程，退出码 0、state 持久化、tmux/kimico 存活；若线程死亡，退出码 5。

## Step 8：测试补齐、systemd 单元、部署手册、全量验收

- 实现：整理 pytest 工程（`tests/`，`pytest` 全绿）；产出部署物（`30-deploy.md` 的 systemd 单元与配置模板）；执行 `20-acceptance.md`。
- 完成定义：
  - `pytest` 全部通过；关键算法有 property-style 测试（用标准库 `random` 生成用例，不引入 hypothesis；`find_overlap` 与暴力实现对比、`chunk_lines` 不变量、`StabilityTracker` 模型）；
  - 按 `30-deploy.md` 在一台干净 Ubuntu 24.04 完成部署，systemd 服务正常运行；
  - `20-acceptance.md` 基础验收 1–10 + 补充验收 11–23 全部通过（假环境），真机联调条目（第 3 节）通过；
  - `systemctl restart` 后 tmux/kimico PID 不变；
  - 配置文件权限、PATH、失败重启策略检查通过。

## 依赖关系速查

```
Step 0 (真实 TUI 门禁)
  └─▶ Step 1 (config/state)
        ├─▶ Step 2 (session 基础)
        │     └─▶ Step 7 (RecoveryManager, __main__)
        ├─▶ Step 3 (input 基础) ──────────────┐
        ├─▶ Step 4 (capture/mirror) ──┐       │
        ├─▶ Step 5 (onebot) ──────────┼───────┤
        └─▶ Step 6 (router + worker) ─┴───────┴─▶ Step 7 ─▶ Step 8
```
