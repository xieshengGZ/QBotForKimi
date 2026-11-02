# 14 · 模块规约：input.py

## 1. 职责

- 入站队列：容量 50 的 FIFO，承接 onebot 传入的事件。
- 入站工作线程：串行消费事件——执行控制指令（副作用 + 回执）或把文本注入 tmux。
- tmux 注入：命名 buffer 的 `set-buffer` → `paste-buffer -r` → `send-keys C-m` 三段式，支持多行文本，避免 shell 转义问题，且不覆盖用户默认粘贴 buffer。

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
    bus: OutboundBus,                 # mirror.OutboundBus
    mirror_switch: MirrorSwitch,      # mirror.MirrorSwitch
    epoch: OutputEpoch,               # mirror.OutputEpoch
    echo: EchoSuppressor,             # mirror.EchoSuppressor
    reset_baseline: threading.Event,  # 请求监控线程重置输出基线（#clear）
    status_provider: Callable[[], StatusSnapshot],  # __main__ 提供
    session: str,
    stop_event: threading.Event,
) -> None:
    """工作线程主循环；stop_event 置位后退出。"""
```

（`PrivateMessageEvent` 来自 onebot；`OutboundBus` / `MirrorSwitch` / `OutputEpoch` / `EchoSuppressor` 来自 mirror；`StatusSnapshot` 来自 router。）

## 3. 注入行为规约

`inject` 依次执行三条 tmux 命令（`subprocess.run`，list 形式传参，**不经 shell**，`timeout=5`）：

```
buf = f"kimico-gw-{os.getpid()}-{threading.get_ident()}-{next(counter)}"
# counter 为模块级 itertools.count()；buf 只需在当前进程内唯一
tmux set-buffer -b <buf> -- <text>
tmux paste-buffer -d -p -r -b <buf> -t =<session>
tmux send-keys -t =<session> C-m
```

- 每条命令检查返回码；非零/超时即抛 `InjectError`，消息含命令名与 stderr 摘要，**不含注入文本**（防止长文本刷屏日志）。
- `-p` 让 bracketed-paste 感知的程序把多行文本视为一次粘贴；`-r` 保留 LF，避免默认把 LF 替换为 CR；随后 `C-m` 提交。顺序不可调整。
- `-b <buf>` 使用唯一命名 buffer，避免覆盖用户默认粘贴 buffer；`-d` 在 paste 成功后删除 buffer。失败时在 `finally` 中执行 `tmux delete-buffer -b <buf>`（忽略删除失败）。
- text 原样传递（可能含多行、空白、引号），由 argv 传递规避转义问题。
- text 含 NUL（`\x00`）时直接抛 `InjectError`（argv 无法承载 NUL）。
- `OSError`、`subprocess.SubprocessError`、`ValueError` 统一包装为 `InjectError`；不裸抛。
- 不做“回答结束”检测；连续消息按到达顺序依次注入。
- 网关不检测本地终端是否正在输入（由操作人员保证同一时刻只一方输入）。

## 4. 工作线程行为规约

循环：`stop_event` 未置位时 `inbound.get(timeout=0.5)`；取到事件后按**下表顺序**处理：

| # | 条件 | 动作 |
|---|---|---|
| 1 | `event.has_text == False` | `bus.put_control(NOT_TEXT_REPLY)` |
| 2 | `event.text.strip() == ""` | 静默忽略，不回复 |
| 3 | `event.text.startswith("##")` | 取 `literal = event.text[1:]`，执行注入；成功 → `echo.register(literal)`；失败 → ERROR + `bus.put_control(INJECT_FAIL_REPLY)`。仅匹配列首 `##`；带前导空白的 `  ##x` 按普通文本处理 |
| 4 | `parse_command(event.text)` 返回 `MIRROR_ON` | `mirror_switch.set(True)` → `bus.put_control(MIRROR_ON_REPLY)` |
| 5 | `MIRROR_OFF` | `mirror_switch.set(False)` → `epoch.bump()` → `bus.clear_bulk()` → `bus.put_control(MIRROR_OFF_REPLY)` |
| 6 | `STATUS` | `format_status(status_provider(), time.monotonic(), time.time())` → `bus.put_control(...)`；异常 → ERROR + `bus.put_control(STATUS_FAIL_REPLY)` |
| 7 | `CLEAR` | `epoch.bump()` → `bus.clear_bulk()` → `reset_baseline.set()` → `bus.put_control(CLEAR_REPLY)` |
| 8 | `UNKNOWN` | `bus.put_control(USAGE_TEXT)` |
| 9 | 返回 None（普通消息，含 `/session`、非空文本） | `inject(session, event.text)`；成功 → `echo.register(event.text)`；失败（`InjectError`）→ 记 ERROR 日志并 `bus.put_control(INJECT_FAIL_REPLY)` |

细则：

- 回执**始终发送**，不受 mirror 开关影响；控制 lane 不因输出溢出丢弃。
- 注入成功**不回复 ACK**（静默）。
- `mirror_switch.set` 触发 mirror 持久化（由 mirror 模块的回调完成，见 `16-mod-mirror.md`），本模块不直接触碰 state。
- 分支 5/7 的顺序不可调整：先切开关/递增 generation，再清输出 lane，最后放回执；控制 lane 保证回执不被清掉。
- `#clear` 的基线重置由监控线程在下一轮执行；generation 已递增，旧输出段不会发送。
- 普通消息注入前**不**做内容修改（不 strip）：用户发什么，kimico 收什么；纯空白已在分支 2 拦截。
- `echo.register` 在 `inject` 返回成功后立即调用（10 秒窗口从此刻起算，见 `16-mod-mirror.md`）。
- 每个事件的处理互不影响：单个事件处理中抛出未预期异常 → 记 ERROR（含 traceback），继续处理下一个事件，线程不退出。
- `bus.put_control` 不阻塞；控制 lane 过长时由 mirror 记 ERROR，worker 不关心。

## 5. 边界条件

- 队列满：由 HTTP 接入侧在 `try_put` 返回 False 时 `bus.put_control(BUSY_REPLY)`（见 `12-mod-onebot.md` 的接线）；本模块不感知。
- 纯空白文本（如 `"   "`、`"\n"`）→ 分支 2 静默忽略，不注入。
- `##` 后为空（消息就是 `##`）→ 注入单个 `#`；`##hello` → 注入 `#hello`。
- 控制指令与普通消息混排时严格按到达顺序处理（单线程串行天然保证）。
- 停机时队列中未处理的事件直接丢弃，不持久化。
- 注入超时（tmux 卡住）→ `InjectError`，回执 + ERROR，不会永久阻塞 worker。

## 6. 错误处理

| 场景 | 处理 |
|---|---|
| InjectError | ERROR 日志（不含注入文本）+ `INJECT_FAIL_REPLY` 回执 |
| status_provider 抛异常 | ERROR 日志；向控制 lane 放入降级回执 `STATUS_FAIL_REPLY` |
| 其他未预期异常 | ERROR 日志（含 traceback），继续循环 |
| tmux delete-buffer 失败 | DEBUG/WARNING，不影响主流程 |

## 7. 日志要求

| 事件 | 级别 |
|---|---|
| 控制指令执行（种类） | INFO（`command executed: MIRROR_ON`） |
| generation 递增 | INFO（`output epoch: N`） |
| 消息注入成功 | DEBUG（仅 message_id 与长度） |
| 注入失败 | ERROR（不含文本） |
| 未预期异常 | ERROR（含 traceback） |

## 8. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_queue_capacity | 放满 50 个 | 第 51 个 try_put 返回 False；get 顺序 FIFO |
| test_queue_timeout | 空队列 get(0.05) | 约 50ms 后返回 None |
| test_inject_commands | mock subprocess.run | 三条命令按序调用；`-b` 命名 buffer、`-r`、`-d`、`=session`、无 shell |
| test_inject_failure | 第二条命令返回码 1 | 抛 InjectError，消息不含注入文本；执行 delete-buffer |
| test_inject_timeout | mock 超时 | InjectError，消息含 timeout |
| test_inject_nul | text 含 `\x00` | InjectError，不执行 tmux |
| test_worker_passthrough | 普通文本事件 | inject 参数原文不变；echo.register 被调用；无回执 |
| test_worker_double_hash | `##hello` | inject 收到 `#hello`；不调用 parse_command；echo.register("#hello") |
| test_worker_inject_fail | inject 抛 InjectError | 控制 lane 收到 `注入失败，请稍后重试` |
| test_worker_not_text | has_text=False | 控制 lane 收到 `仅支持文本消息` |
| test_worker_empty_text | text="" 且 has_text=True | 无任何动作、无回执 |
| test_worker_whitespace | text="   \n" | 无任何动作、无回执 |
| test_worker_mirror_off_order | `#mirror off` | set(False) → epoch.bump → clear_bulk → put_control，顺序正确 |
| test_worker_clear | `#clear` | epoch.bump、clear_bulk、reset_baseline.set 均被调用；控制回执入队 |
| test_worker_status | `#status` | provider 被调用一次，回执为 format_status 结果（8 行） |
| test_worker_status_fail | provider 抛异常 | 控制 lane 收到 STATUS_FAIL_REPLY |
| test_worker_unknown | `#foo` | 控制 lane 收到 USAGE_TEXT |
| test_worker_stop | stop_event 置位 | 线程在 1s 内退出 |
| test_worker_exception_isolation | 某事件处理抛 RuntimeError | 记 ERROR，后续事件仍被处理 |
