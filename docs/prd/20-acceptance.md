# 20 · 验收标准与可执行检查

10 条基础验收 + v1.1 补充验收。多数条目可在**无真实 QQ** 的环境验证：用 curl 模拟 NapCat 事件上报，用本地假 HTTP 服务接收出站消息；涉及真实 TUI/部署的条目单独标注。

## 0. 验收环境准备

### 0.1 假 kimico

把 `tmux.kimico_command` 指向下面的脚本（部署手册中的 config 复制一份改测）：

```bash
#!/usr/bin/env bash
# fake-kimico.sh：打印启动横幅后进入 read 循环，回显并"回答"输入
echo "fake-kimico ready"
while IFS= read -r line; do
  echo "you said: $line"
done
```

多行回答模式 `fake-kimico-multi.sh`（用于验收 2 的回显边界）：

```bash
#!/usr/bin/env bash
echo "fake-kimico-multi ready"
while IFS= read -r line; do
  echo "you said: $line"
  echo "answer line 1"
  echo "answer line 2"
done
```

长输出模式 `fake-kimico-long.sh`：收到 `gen` 时输出 3000 行 `line-0001 ...`（用于验收 6）。不要用 5000 行：`capture_lines=5000` 加上提示符/横幅会截掉开头，测试本身不稳定。

### 0.2 假 NapCat 出站接收器

```bash
python3 - <<'EOF'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = json.loads(body)
        print(f"<<< {msg['message']}", flush=True)
        self.send_response(200); self.send_header("Content-Length","0"); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", 3000), H).serve_forever()
EOF
```

网关配置 `onebot.api_base = "http://127.0.0.1:3000"`，接收器打印的每行 `<<< ...` 即一条 QQ 消息。

### 0.3 模拟事件上报

用 Python 构造 JSON，避免文本中的引号/换行破坏请求体：

```bash
TOKEN=配置里的server.access_token
MAIN=配置里的主QQ号
send_event() {  # $1=消息文本  $2=message_id  $3=user_id(可选)
  body=$(python3 -c '
import json, sys
text, mid, uid = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
print(json.dumps({
  "post_type": "message", "message_type": "private",
  "message_id": mid, "user_id": uid,
  "message": [{"type": "text", "data": {"text": text}}],
}, ensure_ascii=False))
' "$1" "$2" "${3:-$MAIN}")
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8080/cq \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "$body"
}
```

## 1. 基础验收

### 验收 1：mirror 开关与本地输入的联动

> 本地 attach 输入，手机 mirror on 时能看到该回答；mirror off 时手机不收 kimico 输出，但仍收到控制回执。

步骤：
1. `send_event "#mirror off" 101` → 接收器出现 `mirror 已关闭，待发送输出已清空`（回执不受 mirror 影响）。
2. 本地 `tmux attach -t kimico`，在 fake-kimico 输入 `hello`，观察 3 秒：接收器**无** `you said: hello`。
3. `send_event "#mirror on" 102` → 回执 `mirror 已开启`。
4. 本地再输入 `world` → 3~5 秒内接收器出现 `you said: world`。

### 验收 2：手机消息注入与回答回推（含回显边界）

> 手机发送普通消息，本地终端实时看到注入与回答；mirror on 时回答分段回到手机；注入回显不重复，回答不被吞。

步骤：
1. mirror on。`send_event "测试消息A" 103` → 200。
2. 本地 attach 终端实时可见注入的 `测试消息A` 与 fake-kimico-multi 的三行回答。
3. 接收器出现 `you said: 测试消息A`、`answer line 1`、`answer line 2`；**不应**单独出现一条 `测试消息A`（回显被行级抵消），也不应缺少任一回答行。
4. 再发送一条相同文本 `测试消息A`（新 message_id），确认两条回显各自只抵消一次，不互相吃掉。

### 验收 3：连续消息顺序注入

> 手机连续发送两条消息，注入顺序与发送顺序一致，不出现两段文字拼接。

步骤：
1. 同一秒内执行 `send_event "第一条" 104` 与 `send_event "第二条" 105`（或用脚本并发 10 条，检查顺序）。
2. 本地终端显示先 `第一条` 后 `第二条`；接收器依次出现 `you said: 第一条`、`you said: 第二条`，且不存在合并成一条的消息。
3. 并发 10 条时，注入顺序与 message_id 递增顺序一致（单线程 HTTP 接入保证）。

### 验收 4：#status 内容

> #status 返回会话存活、kimico 存活、mirror、入站队列、控制队列、输出队列、最后输出时间、运行时长。

步骤：`send_event "#status" 106` → 接收器出现 **8 行**状态，格式与字段与 `13-mod-router.md` 第 4 节模板一致（此前已产生输出时"最后输出"为时间戳，否则为"无"）。

### 验收 5：#clear

> #clear 后旧缓存不再推送，kimico 上下文不受影响。

步骤：
1. mirror off 状态下本地输入 `clear-test`（产生输出但不在推送队列）。
2. `send_event "#clear" 107` → 回执 `输出缓存已清空，基线已重置（不影响 kimico 上下文）`。
3. mirror on，再本地输入 `after-clear` → 接收器只出现 `you said: after-clear`，**不出现** `clear-test` 的回答。
4. 在 `#clear` 回执后 1 秒内检查接收器，不应出现任何旧 generation 的输出段。
5. kimico 侧验证上下文：连续对话（fake-kimico 回显可见历史仍在终端滚动历史中）。

### 验收 6：长回答切段

> 长回答按 1500 字符切段，段间隔不小于 1s，顺序正确；批处理不丢内容。

步骤：
1. mirror on。向 fake-kimico-long 输入 `gen`（产生 3000 行输出）。
2. 接收器收到多条消息：每条 ≤ 1500 字符；除被硬切的超长行外，每条以完整行结尾（行不被截断）；各段拼接顺序与终端输出一致；相邻段到达间隔 ≥ 1s（接收器打印时间戳人工核对）。
3. 记录接收到的总字符数，与 `tmux capture-pane -p -J -S -5000` 的实际输出比对，允许因滚动/提示符产生的少量差异；不得整段丢失。

### 验收 7：权限与消息类型过滤

> 非主 QQ、群消息被静默忽略；非文本消息收到固定提示。

步骤：
1. `send_event "越权" 108 999999999`（非主 QQ）→ HTTP 200，终端无注入，接收器无消息。
2. 构造 `message_type:"group"` 的事件 → 200，无任何处理。
3. 主 QQ 发送 message 数组仅含 `{"type":"image",...}` 的事件 → 接收器出现 `仅支持文本消息`。
4. 同 message_id 重复上报 → 第二次 200 但不重复处理。
5. 主 QQ 发送 `message` 为字符串的事件 → 400，不注入。

### 验收 8：崩溃恢复

> 手动 kill kimico，网关按退避自动重启并发送 QQ 通知；连续 3 次失败后停止并告警。

步骤：
1. `pkill -f fake-kimico` → 1 个轮询周期内接收器出现 `kimico 已退出，将在 5s 后尝试重启（第 1/3 次）`；约 5s 后 pane 复活（`tmux display-message -p -t kimico '#{pane_dead}'` 为 0）。
2. 测试“启动即 dead 首次等 5s”：停网关，`tmux kill-session -t kimico`，把 `kimico_command` 指向 `false` 之类的秒退命令，再启动网关。首次通知必须是 `5s`，不是 `15s`。
3. 观察通知依次为 5s/15s/60s，随后出现 `kimico 在 600s 内累计恢复失败 3 次，已停止自动重启，请登录机器处理`，不再重试。
4. 恢复成功后 `#status` 显示 `kimico 进程：运行中`。
5. 测试“respawn 失败只计一次”：mock/构造 `respawn-pane` 失败，确认下一次尝试退避档位只升一档，不跳档。

### 验收 9：网关重启的状态恢复与 tmux 存活

> 网关重启后 mirror 状态恢复，不回放重启前的输出，且 tmux/kimico 不被杀掉。

步骤：
1. 记录 `tmux display-message -p -t kimico '#{pid}'`。
2. mirror on 状态下 `systemctl restart kimico-gateway`。
3. 再记录 PID：必须与第 1 步一致；`#{pane_dead}` 为 0。
4. `send_event "#status" 109` → `mirror：on`。
5. 重启后 5 秒内接收器**不出现**重启前终端上的历史输出；之后本地输入 `post-restart` → 接收器只出现其回答。

### 验收 10：本地体验一致性

> 本地 tmux attach 的显示与直接运行 kimico 一致，仅多出网关注入的输入。

步骤：
1. 用同一 `fake-kimico` 启动两个 tmux 会话：`direct`（直接运行）与 `kimico`（网关接管）。
2. 分别注入相同的三条输入，等待稳定后执行：
   ```bash
   tmux capture-pane -p -J -t direct > /tmp/direct.txt
   tmux capture-pane -p -J -t kimico > /tmp/gateway.txt
   diff -u /tmp/direct.txt /tmp/gateway.txt
   ```
3. 允许差异仅限：会话提示符/状态栏、网关注入的输入行；回答正文逐行一致。把允许差异写成 `diff` 过滤规则，验收时执行，而不是人工“看起来一致”。

## 2. v1.1 补充验收

### 验收 11：配置权限与服务用户可读

```bash
sudo -u ubuntu test -r /etc/kimico-gateway/config.toml && echo PASS
```

### 验收 12：history-limit 顺序生效

让 fake-kimico 输出 3000 行后：

```bash
n=$(tmux capture-pane -p -J -S -5000 -t kimico | wc -l)
[ "$n" -gt 2500 ] && echo PASS || echo "FAIL: $n"
```

### 验收 13：启动即 dead 的第一次退避

见验收 8 第 2 步；首次通知为 5s。

### 验收 14：恢复失败只计一次

见验收 8 第 5 步；连续两次 `respawn` 失败不应直接熔断。

### 验收 15：并发清理无旧输出

mirror on，持续让 fake-kimico 输出；循环 100 次发送 `#clear`，每次回执后 1 秒内检查接收器：

- 不得出现回执前已存在的旧 generation 段；
- 不得出现 `#clear` 后仍在发送的旧段（已发出的单个网络请求除外，需在日志中标记）。

`#mirror off` 同理。

### 验收 16：控制 lane 优先级

1. mirror on，让 fake-kimico 产生 500+ 段输出，灌满输出 lane。
2. 发送 `#status`，记录接收器时间；必须在 2 秒内收到 8 行回执。
3. 发送 `#mirror off`，确认输出 lane 被清空、控制回执仍到达；再触发一次恢复告警，确认告警不被 `#mirror off` 清掉。

### 验收 17：回显边界

用 fake-kimico-multi 验证：

- 回显行 + 两行回答在同一个稳定块：只删回显行，回答完整；
- 回显行带 `> ` 前缀：只删该行，回答完整；
- 连续两条相同注入：各自消费一次，不互相吞；
- mirror off 超过 10 秒后再 on：回显队列不无限增长（检查 `#status` 或日志计数）。

### 验收 18：真实 kimico TUI

用真实 kimico 运行 10 分钟，交替本地/手机输入，统计：

- `capture_k0_total` 与 `recover_new_lines` 成功/失败次数；
- 手机端收到的内容与终端最终屏幕的差异（丢失行、重复行）；
- 镜像延迟 p95；
- 多行粘贴是否被 kimico 正确接收、是否意外多次提交。

判定：无回答行丢失；重复行可解释；p95 ≤ 2s。此项不通过，不得冻结 v1.1。

### 验收 19：并发入站顺序

用脚本并发发送 20 条不同文本（不同 message_id），检查本地注入顺序与 message_id 顺序一致；HTTP 单线程接入不得出现乱序。

### 验收 20：安全与边界

- 错误/缺失 token → 401；
- `access_token = "change-me"` → 启动失败，退出码 2；
- `Content-Length: -1` → 400；
- `Content-Length` 超限 → 413 且 `Connection: close`；
- `message` 非 list → 400；
- 非主 QQ/群消息 → 200 静默丢弃；
- 发送端设置 `http_proxy` 指向不可达地址，仍能直连 127.0.0.1:3000。

### 验收 21：tmux 卡死不拖死网关

用 mock 或 `SIGSTOP` tmux server 模拟不响应：

- 所有 tmux 调用在 5s 内超时；
- 监控/输入线程不永久阻塞；
- 日志有 timeout 记录；恢复流程不把 UNKNOWN 当成 DEAD 去 respawn。

### 验收 22：线程死亡可恢复

人为让发送线程抛出未捕获异常（测试钩子）：

- 主线程 1s 内检测到线程死亡；
- 记录 CRITICAL，置位 stop_event，退出码 5；
- systemd 拉起新进程后功能恢复。

### 验收 23：state 损坏与并发写

- 删除/清空/写坏 state.json → 启动为 mirror=false，日志正确；
- 两个线程同时 save → 文件始终是合法 JSON，无 `state.*.tmp` 残留。

## 3. 真机联调（真实 NapCat + QQ）

上述基础验收 1–10 与补充验收 11–23 在假环境通过后，按 `30-deploy.md` 部署真实 NapCat，用主 QQ 重复验收 1、2、3、5、7、16、17、18，确认真链路一致。真机注意：QQ 侧风控可能延迟消息，判定时以到达顺序与内容为准，不苛求间隔精确等于 1s。
