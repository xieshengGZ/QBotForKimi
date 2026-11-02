# 20 · 验收标准与可执行检查

10 条验收标准逐条给出验证方法。多数条目可在**无真实 QQ** 的环境验证：用 curl 模拟 NapCat 事件上报，用本地假 HTTP 服务接收出站消息。

## 0. 验收环境准备

### 0.1 假 kimico（替代真实 kimico，验证输出通道与恢复）

把 `tmux.kimico_command` 指向下面的脚本（部署手册中的 config 复制一份改测）：

```bash
#!/usr/bin/env bash
# fake-kimico.sh：打印启动横幅后进入 read 循环，回显并"回答"输入
echo "fake-kimico ready"
while IFS= read -r line; do
  echo "you said: $line"
done
```

长输出模式 `fake-kimico-long.sh`：收到 `gen` 时输出 5000 行 `line-0001 ...`（用于验收 6）。

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

### 0.3 模拟事件上报（curl 模板）

```bash
TOKEN=配置里的server.access_token
MAIN=配置里的主QQ号
send_event() {  # $1=消息文本  $2=message_id  $3=user_id(可选)
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8080/cq \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"post_type\":\"message\",\"message_type\":\"private\",\"message_id\":$2,\"user_id\":${3:-$MAIN},\"message\":[{\"type\":\"text\",\"data\":{\"text\":\"$1\"}}]}"
}
```

## 1. 验收条目

### 验收 1：mirror 开关与本地输入的联动

> 本地 attach 输入，手机 mirror on 时能看到该回答；mirror off 时手机不收 kimico 输出，但仍收到控制回执。

步骤：
1. `send_event "#mirror off" 101` → 接收器出现 `mirror 已关闭，待发送队列已清空`（回执不受 mirror 影响）。
2. 本地 `tmux attach -t kimico`，在 fake-kimico 输入 `hello`，观察 3 秒：接收器**无** `you said: hello`。
3. `send_event "#mirror on" 102` → 回执 `mirror 已开启`。
4. 本地再输入 `world` → 3~5 秒内接收器出现 `you said: world`。

### 验收 2：手机消息注入与回答回推

> 手机发送普通消息，本地终端实时看到注入与回答；mirror on 时回答分段回到手机。

步骤：
1. mirror on。`send_event "测试消息A" 103` → 200。
2. 本地 attach 终端实时可见注入的 `测试消息A` 与 fake-kimico 的回答。
3. 接收器出现 `you said: 测试消息A`（注入文本本身的回显被抵消，不应单独出现一条 `测试消息A`）。

### 验收 3：连续消息顺序注入

> 手机连续发送两条消息，注入顺序与发送顺序一致，不出现两段文字拼接。

步骤：
1. 同一秒内执行 `send_event "第一条" 104` 与 `send_event "第二条" 105`。
2. 本地终端显示先 `第一条` 后 `第二条`；接收器依次出现 `you said: 第一条`、`you said: 第二条`，两条回答间隔 ≥ 1s，且不存在合并成一条的消息。

自动化覆盖：`14-mod-input.md` 队列与串行测试；`16-mod-mirror.md` test_sender_interval。

### 验收 4：#status 内容

> #status 返回会话存活、kimico 存活、mirror、两个队列长度、最后输出时间、运行时长。

步骤：`send_event "#status" 106` → 接收器出现 7 行状态，格式与字段与 `13-mod-router.md` 第 4 节模板一致（此前已产生输出时"最后输出"为时间戳，否则为"无"）。

自动化覆盖：router 的 format_status 测试 + 集成冒烟。

### 验收 5：#clear

> #clear 后旧缓存不再推送，kimico 上下文不受影响。

步骤：
1. mirror off 状态下本地输入 `clear-test`（产生输出但不在推送队列）。
2. `send_event "#clear" 107` → 回执 `输出缓存已清空，基线已重置（不影响 kimico 上下文）`。
3. mirror on，再本地输入 `after-clear` → 接收器只出现 `you said: after-clear`，**不出现** `clear-test` 的回答。
4. kimico 侧验证上下文：连续对话（fake-kimico 回显可见历史仍在终端滚动历史中）。

### 验收 6：长回答切段

> 长回答按 1500 字符切段，段间隔不小于 1s，顺序正确。

步骤：
1. mirror on。向 fake-kimico-long 输入 `gen`（产生 5000 行输出，总量 > 1500 字符数倍）。
2. 接收器收到多条消息：每条 ≤ 1500 字符；除被硬切的超长行外，每条以完整行结尾（行不被截断）；各段拼接顺序与终端输出一致；相邻段到达间隔 ≥ 1s（接收器打印时间戳人工核对）。

自动化覆盖：`16-mod-mirror.md` test_chunk_*；间隔由 test_sender_interval 覆盖。

### 验收 7：权限与消息类型过滤

> 非主 QQ、群消息被静默忽略；非文本消息收到固定提示。

步骤：
1. `send_event "越权" 108 999999999`（非主 QQ）→ HTTP 200，终端无注入，接收器无消息。
2. 构造 `message_type:"group"` 的事件 → 200，无任何处理。
3. 主 QQ 发送 message 数组仅含 `{"type":"image",...}` 的事件 → 接收器出现 `仅支持文本消息`。
4. 同 message_id 重复上报 → 第二次 200 但不重复处理。

### 验收 8：崩溃恢复

> 手动 kill kimico，网关按退避自动重启并发送 QQ 通知；连续 3 次失败后停止并告警。

步骤：
1. `pkill -f fake-kimico` → 1 个轮询周期内接收器出现 `kimico 已退出，将在 5s 后尝试重启（第 1/3 次）`；约 5s 后 pane 复活（`tmux display-message -p -t kimico '#{pane_dead}'` 为 0）。
2. 让 kimico 启动后立即退出（临时把 config 的 `kimico_command` 指向 `false` 之类的秒退命令并重启网关）：观察通知依次为 5s/15s/60s，随后出现 `kimico 连续恢复失败 3 次，已停止自动重启，请登录机器处理`，不再重试。
3. 恢复成功后 `#status` 显示 `kimico 进程：运行中`。

自动化覆盖：`17-mod-session.md` RecoveryManager 全部测试。

### 验收 9：网关重启的状态恢复

> 网关重启后 mirror 状态恢复，且不回放重启前的输出。

步骤：
1. mirror on 状态下 `systemctl restart kimico-gateway`。
2. `send_event "#status" 109` → `mirror：on`。
3. 重启后 5 秒内接收器**不出现**重启前终端上的历史输出；之后本地输入 `post-restart` → 接收器只出现其回答。

### 验收 10：本地体验一致性

> 本地 tmux attach 的显示与直接运行 kimico 一致，仅多出网关注入的输入。

步骤：
1. 开两个终端：A 直接运行 fake-kimico；B attach 到网关的 tmux 会话。
2. 各自输入若干命令比对显示格式（提示符、换行、颜色）：一致。
3. B 中可见 QQ 注入的消息与回答；A 中无（证明注入只经 tmux 进入）。

## 2. 真机联调（真实 NapCat + QQ）

上述 1–10 在假环境通过后，按 `30-deploy.md` 部署真实 NapCat，用主 QQ 重复验收 1、2、3、5、7，确认真链路一致。真机注意：QQ 侧风控可能延迟消息，判定时以到达顺序与内容为准，不苛求间隔精确等于 1s。
