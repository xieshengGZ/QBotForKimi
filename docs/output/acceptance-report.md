# 验收报告（ticket 16）

日期：2026-11-02
分支：`feat/16-acceptance-gate`（worktree `/home/ubuntu/qq/.worktrees/16`）
基线：`main @ 79bca90`（388 passed）

结论：**通过**。假环境验收 20 项 PASS、0 FAIL；systemd 的验收 9/11 PASS；真实 `kimi 2.4.1` 门禁 PASS（338.33 秒、39/39 标签、0 丢失、0 重复回答行、p95 1.662s）。验收过程中发现并修复了 2 个真实缺陷（见 §4）。

## 1. 环境与隔离

- Python：`/home/ubuntu/qq/.venv/bin/python`（3.12）；tmux 3.4；systemd 255；`kimi 2.4.1` 位于 `/home/ubuntu/.kimi-code/bin/kimi`。
- **端口 3000 被无关容器 `shotsmith-web-1` 占用**。本套件没有停止/删除任何 `shotsmith-*` 容器，也没有占用 3000；假 NapCat 接收器绑定随机空闲回环端口，并把 `onebot.api_base` 指向该端口。这是与 PRD 0.2 的唯一环境偏离，已记录。
- 每个 fake 环境使用独立的 `tempfile.mkdtemp()`、独立 `TMUX_TMPDIR`、唯一 session 名和随机 HTTP 端口。结束只 `kill-server` 自己的 tmux 目录、只杀自己的 session；用户默认 tmux 的 `kimi02`/`qq-6`/`test` 在每次运行前后均存在且未被触碰。
- systemd 检查使用已安装的 `kimico-gateway.service` 与 `/etc/kimico-gateway/config.toml`，结束后恢复 `stop` + `disable`；只清理本次创建的 `kimico` session。
- 真实 kimi 门禁使用独立 `TMUX_TMPDIR`，不连接默认 tmux server。

## 2. 命令

```bash
cd /home/ubuntu/qq/.worktrees/16

# 假环境验收（约 2 分钟，20 PASS / 3 SKIP）
/home/ubuntu/qq/.venv/bin/python acceptance/run_acceptance.py \
  --json-out /tmp/acceptance-final.json

# systemd 验收 9/11
/home/ubuntu/qq/.venv/bin/python acceptance/systemd_check.py \
  --json-out /tmp/systemd-final.json

# 真实 kimi 门禁（338 秒）
/home/ubuntu/qq/.venv/bin/python acceptance/real_kimico_gate.py \
  --min-duration 330 \
  --out /tmp/real-kimi-final.json \
  --dump-messages /tmp/real-kimi-final.messages.json

# 单元/回归测试
/home/ubuntu/qq/.venv/bin/python -m pytest -q
```

## 3. 验收逐项结果

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| 1 | mirror 开关与本地输入 | PASS | mirror off 时本地 `hello` 无 `you said: hello`；mirror on 后收到 `you said: world` |
| 2 | 手机注入与回答回推、回显边界 | PASS | 两次相同注入各送达 3 行回答；接收端无单独的回显行 |
| 3 | 连续消息顺序注入 | PASS | 顺序注入 10 条，pane 顺序 `seq-000..seq-009`；10 条并发各注入一次、无拼接 |
| 4 | `#status` 内容 | PASS | 恰好 8 行，字段顺序与 PRD 模板一致 |
| 5 | `#clear` | PASS | 回执后 1s 内无旧 generation；mirror on 后只出现 `after-clear`；终端仍保留 `clear-test` 回答 |
| 6 | 长回答切段 | PASS | 3000 行全部送达且顺序正确；21 条消息均 ≤1500 字符；最小段间隔 1.023s |
| 7 | 权限与消息类型过滤 | PASS | 非主 QQ/群消息静默；image → `仅支持文本消息`；重复 message_id 只处理一次；字符串 message → 400 |
| 8 | 崩溃恢复 | PASS | kill 后通知 `将在 5s 后尝试重启（第 1/3 次）`；pane 472634→473084 复活；`#status` 显示运行中 |
| 9 | 网关重启后 tmux/kimico PID 不变 | PASS | `systemctl restart` 前后 tmux server PID 2301971 与 pane PID 479886 均不变；`pane_dead=0`；网关 MainPID 479880→479908 |
| 10 | 本地体验一致性 | PASS | direct 与 gateway pane 的过滤后回答行完全一致 |
| 11 | 配置权限与服务用户可读 | PASS | `600 ubuntu:ubuntu`；`sudo -u ubuntu test -r` 成功 |
| 12 | history-limit 顺序生效 | PASS | `capture-pane -S -5000` 返回 3003 行；3000/3000 行标记存在 |
| 13 | 启动即 dead 的首次 5s 退避 | PASS | 预创建 dead pane 后首次通知为 `将在 5s 后…（第 1/3 次）` |
| 14 | 恢复失败只计一次 | PASS | 连续失败 respawn 的 sleep 序列 `[5, 15, 60]`，不跳档；3 次后熔断且只告警一次 |
| 15 | 并发清理无旧输出 | PASS | 100 次 `#clear`，0 个 BUSY；clear+1.5s 后 0 条旧 generation；mirror off+2s 后 0 条 bulk |
| 16 | 控制 lane 优先级 | PASS | 输出 lane 受压时 `#status` 1.003s 到达（目标 2s）；`#mirror off` 0.151s；输出队列 0/500；控制告警不被 `clear_bulk` 清掉 |
| 17 | 回显边界 | PASS | 多行块保留两行回答；`> ` 前缀回显只删该行；重复相同注入各消费一次；回显队列 5/5 上限并在 10s 后裁剪到 1 |
| 18 | 真实 kimico TUI | PASS | 见 §5：338.33s、39/39、0 丢失、0 重复回答行、p95 1.662s |
| 19 | 并发入站顺序 | PASS | 20 条并发事件各注入一次、无拼接；单线程顺序由验收 3 的顺序注入验证 |
| 20 | 安全与边界 | PASS | 401 错误/缺失 token；`change-me` 启动退出 2；`-1` Content-Length→400；超限→413 + `Connection: close`；非 list→400；非主/群→200 静默；不可达 `http_proxy` 下仍直连回环接收器 |
| 21 | tmux 卡死不拖死网关 | PASS | `get_pane_state` 5.01s 返回 UNKNOWN；`inject` 两次 5s tmux 调用后失败（10.01s）；`respawn` 5.00s 超时；监控日志记录 UNKNOWN 且未 respawn；tmux 恢复后网关与 pane 恢复 |
| 22 | 线程死亡可恢复 | PASS | 1s 内检测、CRITICAL `thread died: sender`、stop_event、退出码 5、state 保存；systemd `Restart=on-failure` 已从 unit 验证（未注入 systemd 自动重启） |
| 23 | state 损坏与并发写 | PASS | 缺失/空/损坏 state 均 mirror=false；20 线程并发 save 后 JSON 合法且无 `state.*.tmp`；损坏启动日志正确 |

假环境套件汇总：`20 PASS, 0 FAIL, 3 SKIP`。SKIP 仅表示由独立脚本覆盖：

- 验收 9/11 → `acceptance/systemd_check.py`（PASS）
- 验收 18 → `acceptance/real_kimico_gate.py`（PASS）

### 3.1 计时/模拟偏离说明

- 验收 8 的假环境 e2e 使用 `success_reset_seconds=2`（生产默认 60）以缩短“健康运行”等待；退避表 `5/15/60`、首次 5s、失败计数逻辑均未改动。生产默认 60s 的语义由 `tests/test_recovery.py` 覆盖。
- 验收 14 使用确定性 `RecoveryManager` + 假时钟（生产退避表与 600s 窗口），避免 80s 真实等待。
- 验收 21 使用受控的“挂起 tmux” shim（PATH 前置一个 sleep 120s 的 `tmux` 包装器），因为在本机 tmux 3.4 上对 server 发 `SIGSTOP` 后 `has-session` 立即返回 MISSING，而不是可观测的挂起。PRD 允许“mock 或 SIGSTOP”，这里选 mock。
- 验收 22 的 systemd 自动拉起没有注入（需要测试钩子或干扰已安装服务）；进程级检测、CRITICAL、退出码 5 已实测，systemd 重启策略从 unit 验证。

## 4. 验收暴露并修复的真实缺陷

### 4.1 并发入站连接被重置（验收 19）

- 现象：20 条并发 HTTP 上报时出现 `ConnectionResetError: [Errno 104] Connection reset by peer`。
- 根因：`kimico_gateway/onebot.py` 的 `_IngressHTTPServer` 沿用 `socketserver.TCPServer.request_queue_size = 5` 的默认监听 backlog；单线程 accept 循环来不及排空 20 个同时到达的连接，内核重置多余连接。
- 修复：`_IngressHTTPServer.request_queue_size = 64`。
- 回归：`tests/test_onebot_ingress.py::test_server_backlog_accepts_twenty_concurrent_requests`。

### 4.2 真实 kimi 的 `❯` 注入回显未被识别（真实门禁）

- 现象：真实 kimi 用 `❯`（U+276F）显示用户注入消息；默认 `echo_prefix_patterns = ["^\\s*[>›]\\s*"]` 不含 `❯`，注入提示的行级回显会被镜像回手机。
- 根因：默认配置只覆盖 `>`/`›`，没有覆盖 kimi TUI 实际使用的 `❯`。
- 修复：`kimico_gateway/config.py` 默认值与 `deploy/config.toml.example` 改为 `^\s*[>›❯]\s*`。
- 回归：`tests/test_config_contract.py` 默认值断言；`tests/test_mirror_pipeline.py::test_echo_kimi_chevron_prompt_prefix_is_stripped`。
- 已知限制：长提示会软换行，`EchoSuppressor` 仍是行级匹配；第一行 `❯ …` 被抑制，换行后的续行可能仍被镜像（真实门禁 13 条消息中 6 条含提示续行）。这不是回答行丢失/重复，回答标签仍 39/39、回答行重复 0；如需完全抑制多行回显，需要把回显匹配扩展到“连续行块”，留作后续改进。

## 5. 真实 kimi 门禁统计

运行：`acceptance/real_kimico_gate.py --min-duration 330`，run id `R603400`，模型 DeepSeek Flash，kimi 2.4.1。

| 指标 | 值 |
|---|---|
| 实际运行时长 | **338.33s（5.64 分钟）** |
| capture 轮数 / 错误 | 1083 / **0** |
| `capture_k0_total` / `k_rounds` | **43 / 1082（3.97%）** |
| `recover_new_lines` | 调用 7，成功 7，失败 0 |
| 期望标签 / 收到 / 送达 | **39 / 39 / 39** |
| 丢失标签 / 丢失回答行 | **0 / 0** |
| 重复标签提及 / 重复回答行 | 0 / **0** |
| 镜像延迟 p50 / p95 / max | **1.621s / 1.662s / 1.662s**（目标 p95 ≤ 2s） |
| 接收端消息数 | 13 |
| 提示续行回显消息 | 6（见 §4.2 已知限制，非回答行） |

驱动方式：3 个 12 行唯一标签长回答（HTTP 注入）+ 3 个单标签短回答（本地 tmux 输入），交替本地/手机。长回答标签格式 `RK-R603400-A-001 a` 等；短回答 `RK-R603400-B-001 OK`。未发生答案行丢失，重复回答行为 0。

## 6. 交付文件

新增/修改：

- `acceptance/fake_kimico.py`、`acceptance/fake_napcat.py`
- `acceptance/harness.py`、`acceptance/run_acceptance.py`
- `acceptance/systemd_check.py`、`acceptance/real_kimico_gate.py`
- `tests/test_acceptance_harness.py`
- `kimico_gateway/onebot.py`（backlog 修复）、`kimico_gateway/config.py`（`❯` 回显前缀）
- `deploy/config.toml.example`（同步 `❯`）
- `tests/test_onebot_ingress.py`、`tests/test_mirror_pipeline.py`、`tests/test_config_contract.py`（回归）
- `docs/output/acceptance-report.md`（本文件）

测试结果：`pytest -q` → **403 passed**（基线 388 + 15 个新增/回归用例）。

## 7. 未完成项 / 假设

- systemd 自动拉起（验收 22 后半）未注入验证；只验证了进程级退出码 5 与 unit 的 `Restart=on-failure`。若要端到端验证，需要给网关增加测试钩子或临时替换已安装 unit。
- 真实 NapCat + QQ 真机联调（PRD 第 3 节）未执行：本机只有 fake NapCat 接收器，没有可用 QQ 账号；因此验收 1/2/3/5/7/16/17/18 的真链路未做。假环境与真实 kimi 已覆盖逻辑路径。
- 多行注入回显的续行仍可能被镜像（§4.2），作为已知限制记录。
- 真实门禁只测了一个模型（DeepSeek Flash）、一个 pane 尺寸和 kimi 2.4.1；不同模型的流式重绘细节可能不同。
