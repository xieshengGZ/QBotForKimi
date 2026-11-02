# 14 · 模块规约：input.py

## 1. 职责

- 入站队列：容量 50 的 FIFO，承接 onebot 传入的事件。
- 入站工作线程：串行消费事件——执行控制指令（副作用 + 回执）或把文本注入 tmux。
- tmux 注入：`set-buffer` → `paste-buffer` → `send-keys C-m` 三段式，支持多行文本，避免 shell 转义问题。

## 2. 接口签名

```python
class InjectError(Exception):
    """tmux 注入失败。message 含失败命令与 stderr 摘要（不含注入文本本身）。"""

class InboundQueue:
    """FIFO，容量固定 50。线程安全。"""
    CAPACITY: int = 50
    def __init__(self, capacity: int = 50) -> None: ...
    def try_put(self, event: PrivateMessageEvent) -> bool:
        """队列满返回 False，否则入队返回 True。不阻塞。"""
    def get(self, timeout: float = 0.5) -> PrivateMessageEvent | None:
        """阻塞至多 timeout 秒；超时返回 None（供 stop_event 轮询退出）。"""
    def qsize(self) -> int: ...

def inject(session: str, text: str) -> None:
    """把 text 注入 session 的活动 pane 并回车。失败抛 InjectError。"""

def run_input_worker(
    inbound: InboundQueue,
    outbound: OutboundQueue,          # mirror.OutboundQueue
    mirror_switch: MirrorSwitch,      # mirror.MirrorSwitch
    echo: EchoSuppressor,             # mirror.EchoSuppressor
    reset_baseline: threading.Event,  # 请求监控线程重置输出基线（#clear）
    status_provider: Callable[[], StatusSnapshot],  # __main__ 提供
    session: str,
    stop_event: threading.Event,
) -> None:
    """工作线程主循环；stop_event 置位后退出。"""
```

（`PrivateMessageEvent` 来自 onebot；`OutboundQueue` / `MirrorSwitch` / `EchoSuppressor` 来自 mirror；`StatusSnapshot` 来自 router。）

## 3. 注入行为规约

`inject` 依次执行三条 tmux 命令（`subprocess.run`，list 形式传参，**不经 shell**）：

```
tmux set-buffer -- <text>
tmux paste-buffer -p -t <session>
tmux send-keys -t <session> C-m
```

- 每条命令检查返回码；非零即抛 `InjectError`，消息含命令名与 stderr 摘要，**不含注入文本**（防止长文本刷屏日志）。
- `-p` 让 bracketed-paste 感知的程序把多行文本视为一次粘贴；随后 `C-m` 提交。顺序不可调整。
- text 原样传递（可能含多行、空白、引号），由 argv 传递天然规避转义问题。
- 不做"回答结束"检测；连续消息按到达顺序依次注入。
- 网关不检测本地终端是否正在输入（由操作人员保证同一时刻只一方输入）。

## 4. 工作线程行为规约

循环：`stop_event` 未置位时 `inbound.get(timeout=0.5)`；取到事件后按下表分支处理（顺序即判定顺序）：

| # | 条件 | 动作 |
|---|---|---|
| 1 | `event.has_text == False` | `outbound.put(NOT_TEXT_REPLY)` |
| 2 | `event.text == ""`（有 text 段但拼接为空串） | 静默忽略，不回复 |
| 3 | `parse_command(event.text)` 返回 `MIRROR_ON` | `mirror_switch.set(True)` → `outbound.put(MIRROR_ON_REPLY)` |
| 4 | `MIRROR_OFF` | `mirror_switch.set(False)` → `outbound.clear()` → `outbound.put(MIRROR_OFF_REPLY)` |
| 5 | `STATUS` | `outbound.put(format_status(status_provider(), time.monotonic(), time.time()))` |
| 6 | `CLEAR` | `outbound.clear()` → `reset_baseline.set()` → `outbound.put(CLEAR_REPLY)` |
| 7 | `UNKNOWN` | `outbound.put(USAGE_TEXT)` |
| 8 | 返回 None（普通消息，含 `/session`、纯空白文本） | `inject(session, event.text)`；成功 → `echo.register(event.text)`；失败（`InjectError`）→ 记 ERROR 日志并 `outbound.put(INJECT_FAIL_REPLY)` |

细则：

- 回执**始终发送**，不受 mirror 开关影响。
- 注入成功**不回复 ACK**（静默）。
- `mirror_switch.set` 触发 mirror 持久化（由 mirror 模块的回调完成，见 `16-mod-mirror.md`），本模块不直接触碰 state。
- 分支 4 中先清队列再放回执，保证回执不被清掉；分支 6 同理。
- 普通消息注入前**不**做内容修改（strip 也不做）：用户发什么，kimico 收什么。
- `echo.register` 在 `inject` 返回成功后立即调用（10 秒窗口从此刻起算，见 `16-mod-mirror.md`）。
- 每个事件的处理互不影响：单个事件处理中抛出未预期异常 → 记 ERROR（含 traceback），继续处理下一个事件，线程不退出。

## 5. 边界条件

- 队列满：由 HTTP 接入侧在 `try_put` 返回 False 时发送 `BUSY_REPLY`（见 `12-mod-onebot.md` 的接线）；本模块不感知。
- 纯空白文本（如 `"   "`、`"\n"`）→ 走分支 8 原样注入。
- 控制指令与普通消息混排时严格按到达顺序处理（单线程串行天然保证）。
- 停机时队列中未处理的事件直接丢弃，不持久化。

## 6. 错误处理

| 场景 | 处理 |
|---|---|
| InjectError | ERROR 日志（不含注入文本）+ `INJECT_FAIL_REPLY` 回执 |
| status_provider 抛异常 | ERROR 日志；向队列放入降级回执 `STATUS_FAIL_REPLY` |
| 其他未预期异常 | ERROR 日志（含 traceback），继续循环 |

## 7. 日志要求

| 事件 | 级别 |
|---|---|
| 控制指令执行（种类） | INFO（`command executed: MIRROR_ON`） |
| 消息注入成功 | DEBUG（仅 message_id 与长度） |
| 注入失败 | ERROR（不含文本） |
| 未预期异常 | ERROR（含 traceback） |

## 8. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_queue_capacity | 放满 50 个 | 第 51 个 try_put 返回 False；get 顺序 FIFO |
| test_queue_timeout | 空队列 get(0.05) | 约 50ms 后返回 None |
| test_inject_commands | mock subprocess.run | 三条命令按序调用，参数与第 3 节一致，list 形式无 shell |
| test_inject_failure | 第二条命令返回码 1 | 抛 InjectError，消息不含注入文本 |
| test_worker_passthrough | 普通文本事件 | inject 被调用且参数原文不变；echo.register 被调用；无回执 |
| test_worker_inject_fail | inject 抛 InjectError | 队列收到 `注入失败，请稍后重试` |
| test_worker_not_text | has_text=False | 队列收到 `仅支持文本消息` |
| test_worker_empty_text | text="" 且 has_text=True | 无任何动作、无回执 |
| test_worker_mirror_off_order | `#mirror off` | set(False) 先于 clear()，clear() 先于 put(回执) |
| test_worker_clear | `#clear` | clear() 与 reset_baseline.set() 均被调用，回执入队 |
| test_worker_status | `#status` | provider 被调用一次，回执为 format_status 结果 |
| test_worker_unknown | `#foo` | 回执为 USAGE_TEXT |
| test_worker_stop | stop_event 置位 | 线程在 1s 内退出 |
| test_worker_exception_isolation | 某事件处理抛 RuntimeError | 记 ERROR，后续事件仍被处理 |
