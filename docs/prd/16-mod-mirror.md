# 16 · 模块规约：mirror.py

## 1. 职责

输出通道的后半段：mirror 开关管理、输出 generation、行过滤、回显抵消、静默窗口批处理、切段、双 lane 发送队列、限速发送。接收 capture 释放的稳定行，产出进入输出 lane 的文本段；发送线程把控制 lane 优先投递到 NapCat。

v1.1 的关键变化：

- `OutboundQueue` 拆为 `OutboundBus`：控制 lane（回执/通知/告警，不因输出溢出丢弃）+ 输出 lane（可丢最旧）。
- 新增 `OutputEpoch`：`#clear` / `#mirror off` 递增 generation，旧段在发送前丢弃。
- 回显改为行级 `suppress`，不再整块跳过。
- 增加静默窗口批处理，减少 QQ 刷屏。

## 2. 接口签名

```python
class MirrorSwitch:
    """mirror 开关，线程安全。on_change 回调用于持久化（__main__ 接线到 StateStore.save）。"""
    def __init__(self, initial: bool, on_change: Callable[[bool], None] | None = None) -> None: ...
    def is_on(self) -> bool: ...
    def set(self, value: bool) -> None:
        """更新并调用 on_change(value)。重复设置同值也触发回调（幂等持久化）。
        回调在锁外调用，避免磁盘 I/O 阻塞 is_on。"""

class OutputEpoch:
    """输出 generation，线程安全。"""
    def __init__(self) -> None: ...
    def current(self) -> int: ...
    def bump(self) -> int:
        """递增并返回新值；#clear / #mirror off 调用。"""

class Lane(Enum):
    CONTROL = auto()
    BULK = auto()

@dataclass(frozen=True)
class OutboundItem:
    text: str
    lane: Lane
    generation: int | None   # 控制 lane 为 None；输出 lane 为产生时的 epoch

class OutboundBus:
    """双 lane 发送总线。线程安全。"""
    def __init__(self, bulk_max: int, overflow_notice: str,
                 control_soft_max: int = 100) -> None: ...
    def put_control(self, text: str) -> None:
        """控制 lane 入队；不阻塞、不因 bulk 溢出丢弃。
        当前控制队列长度达到 control_soft_max 时记 ERROR 并继续入队。"""
    def put_bulk(self, text: str, generation: int) -> None:
        """输出 lane 入队；满时丢最旧，必要时补一条溢出提示。"""
    def get(self, timeout: float = 0.5) -> OutboundItem | None:
        """优先取控制 lane；两者都空时阻塞至多 timeout 秒。"""
    def clear_bulk(self) -> None:
        """只清输出 lane；控制 lane 与溢出提示状态不受影响。"""
    def qsize_control(self) -> int: ...
    def qsize_bulk(self) -> int: ...

def normalize(text: str) -> str:
    """归一化：按行切分 → 逐行 strip → 丢弃空行 → 以空格连接 →
    折叠所有连续空白为单个空格 → strip。用于回显比对。"""

class EchoSuppressor:
    """回显待抵消队列（FIFO）。线程安全。"""
    def __init__(self, window_seconds: float = 10.0,
                 max_entries: int = 50,
                 prefix_patterns: list[re.Pattern] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None: ...
    def register(self, text: str) -> None:
        """登记一条注入文本的回显，窗口 window_seconds 内有效；登记前清理过期项，
        超过 max_entries 时丢最旧。"""
    def suppress(self, lines: list[str]) -> list[str]:
        """行级抵消：清理过期项；若某行 normalize 后等于队首注入文本，
        或去掉 prefix_patterns 前缀后等于队首，则删除该行并消费该项；
        其余行保持原顺序返回。不整块丢弃。"""

def passes_filters(line: str, patterns: list[re.Pattern]) -> bool:
    """line 不匹配任何 pattern（re.search）时返回 True（放行）。"""

def compile_patterns(patterns: list[str] | tuple[str, ...]) -> list[re.Pattern]: ...

def chunk_lines(lines: list[str], max_chars: int) -> list[str]:
    """把行列表切成若干发送段（规则见第 4 节）。"""

class MirrorPipeline:
    """稳定行处理管道：开关 → generation → 过滤 → 回显 → 批处理 → 切段 → 输出 lane。
    仅监控线程调用 handle/flush；clear 仅监控线程调用。"""
    def __init__(self, switch: MirrorSwitch, epoch: OutputEpoch,
                 echo: EchoSuppressor, bus: OutboundBus,
                 patterns: list[re.Pattern],
                 chunk_chars: int,
                 batch_quiet_ms: int,
                 batch_max_chars: int) -> None: ...
    def handle_stable_lines(self, lines: list[str], generation: int) -> None: ...
    def flush_if_due(self, now: float | None = None) -> None:
        """静默窗口到期或批量达到上限时切段入队；监控线程每轮调用。"""
    def clear(self) -> None:
        """清空批处理缓冲；#clear/respawn 时由监控线程调用。"""

def run_sender_loop(
    bus: OutboundBus,
    epoch: OutputEpoch,
    api_base: str,
    access_token: str,
    user_id: int,
    send_interval: float,          # 秒，输出 lane 段间间隔
    control_interval: float,       # 秒，控制 lane 段间最小间隔
    stop_event: threading.Event,
) -> None:
    """发送线程主循环；先控制 lane，后输出 lane；stop_event 置位后退出。"""
```

## 3. 开关与 generation 语义

- `mirror=off`：`handle_stable_lines` 直接丢弃输入，不做过滤/回显/切段；基线已在 capture 侧推进，重新开启后不回放旧内容。
- `#mirror off`：入站 worker 依次执行 `switch.set(False)` → `epoch.bump()` → `bus.clear_bulk()` → `bus.put_control(MIRROR_OFF_REPLY)`。
- `#clear`：入站 worker 依次执行 `epoch.bump()` → `bus.clear_bulk()` → `reset_baseline.set()` → `bus.put_control(CLEAR_REPLY)`；监控线程在下一轮调用 `pipeline.clear()` 并重置基线。
- mirror 状态持久化：__main__ 将 `MirrorSwitch.on_change` 接到 `StateStore.save`；启动时以 `StateStore.load()` 为初始值。
- 发送线程在发送前、重试前检查 `item.generation`；若与 `epoch.current()` 不一致，直接丢弃该段。控制 lane 的 `generation=None`，永不被 generation 检查丢弃。
- 已经在网络请求中的单个段无法撤回；文档明确“已发出的单个请求可能仍在途”，但清空后不会再从队列取出旧 generation 的段。

## 4. 切段规则（chunk_lines）

按整行聚合，尽量不拆行；超长单行硬切：

```
def chunk_lines(lines, max_chars):
    chunks, cur = [], ""
    for line in lines:
        while len(line) > max_chars:
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        cand = line if cur == "" else cur + "\n" + line
        if len(cand) > max_chars:
            chunks.append(cur)
            cur = line
        else:
            cur = cand
    if cur != "":
        chunks.append(cur)
    return chunks
```

- 段内行间以 `\n` 连接；`\n` 计入长度。
- 字符数按 Python str 长度（Unicode code point）计。
- 输入空列表 → 返回 `[]`。

## 5. 双 lane 与溢出规则

### 控制 lane

- 无界队列；`put_control` 不阻塞、不丢弃。
- 当前长度达到 `control_soft_max`（默认 100）时记 ERROR（`control backlog`），但仍继续入队；用于发现发送线程卡死。
- 控制 lane 包含：所有 `#` 指令回执、`BUSY_REPLY`、`NOT_TEXT_REPLY`、`INJECT_FAIL_REPLY`、`STATUS_FAIL_REPLY`、重启通知、熔断告警、溢出提示可放控制 lane（见下）。
- `#mirror off` / `#clear` 不清控制 lane。

### 输出 lane

- 容量 `outbound_queue_max`（默认 500）。
- `put_bulk` 时若已满：循环丢弃队首直到有空位；若本次发生丢弃且输出 lane 中当前没有未发送的溢出提示，则追加一条 `OVERFLOW_NOTICE`（作为 `BULK` 项，generation 用当前值）。
- 溢出提示本身也参与容量；若追加后再次超限，继续丢最旧。
- `clear_bulk` 清空全部输出项（含未发送的溢出提示），重置提示状态。
- 输出 lane 的丢弃只影响 kimico 输出，不影响控制回执/告警。

### 发送顺序

- `OutboundBus.get` 先取控制 lane，再取输出 lane。
- 控制段之间等待 `control_interval`（默认 0.1s）；输出段之间等待 `send_interval`（默认 1s）。
- 同一 lane 内 FIFO；跨 lane 不保证严格时序，但控制优先。

## 6. 发送循环规则（run_sender_loop）

```
while not stop_event.is_set():
    item = bus.get(timeout=0.5)
    if item is None:
        continue
    if item.generation is not None and item.generation != epoch.current():
        log debug（stale generation dropped）
        continue
    try:
        send_private_msg(api_base, access_token, user_id, item.text)   # 超时 3s
    except OnebotAPIError as e:
        log warning（首败重试，不含正文）
        if not stop_event.wait(2):
            continue                      # 停机中，放弃重试
        if item.generation is not None and item.generation != epoch.current():
            log debug（stale before retry）
            continue
        try:
            send_private_msg(...)
        except OnebotAPIError as e:
            log error（放弃该段，只记长度与原因）
        except Exception:
            log error（含 traceback，不含正文）
    except Exception:
        log error（含 traceback，不含正文）
    if item.lane == Lane.BULK:
        stop_event.wait(send_interval)
    else:
        stop_event.wait(control_interval)
```

- 每处理完一段（成功或最终失败）等待对应间隔。
- 不重试第二次以上；不阻塞后续段。
- 停机：`get` 超时轮询 + `stop_event.wait` 保证 1s 内可退出；队列中未发送内容丢弃，不持久化。
- 任何单段异常不得让发送线程退出；外层每轮 catch-all，必要时由 __main__ 健康检查重启网关。

## 7. 过滤、回显与批处理细则

- `ignore_patterns` 默认 `["^\\s*$"]`（丢弃纯空白行）。逐行判定，命中的行直接丢弃；使用 `re.search`。
- 处理顺序：mirror 开关 → generation → 行过滤 → 回显 `suppress` → 批处理 → 切段入队。
- 回显匹配以**行**为单位：
  - 对过滤后的每一行，先 `normalize(line)`，与队首 `normalize(injected)` 比较；
  - 若不等，再逐个 `echo_prefix_patterns` 去掉前缀后比较；
  - 命中则删除该行、消费队首；其余行保留。
  - 若整块都不命中，不消费队首，整块保留。
- 回显队列窗口从 `register` 调用时刻起算 10 秒；过期项在 `register` 和 `suppress` 时清理；最多 50 条。
- 批处理：
  - `handle_stable_lines` 把过滤/回显后的行追加到 `pending_lines`，记录 `last_release = now`。
  - `flush_if_due` 在 `now - last_release >= batch_quiet_ms/1000` 或 `pending` 总字符数 ≥ `batch_max_chars` 时切段入队。
  - `pending` 的 generation 必须与当前 epoch 一致；不一致时直接清空。
  - 输出 lane 的段按 flush 顺序进入，保持行序。
- 单行恰好 `max_chars`：不硬切；与前行拼接超长时单独成段。
- 单行 `max_chars + 1`：硬切成 `max_chars` + `1` 两段。
- 连续注入两条相同文本：登记两项；第一个命中行消费第一项，第二个命中行消费第二项。
- `send_interval = 0`：不等待，连续发送。
- 控制段不受 `send_interval` 影响，只受 `control_interval` 约束。

## 8. 边界条件

- 回显项过期未被命中：`register`/`suppress` 时清除，不影响后续输出。
- mirror off 时 `register` 仍登记，但 `suppress` 不调用；`register` 会清理过期项并限制 50 条，不会无限增长。
- 发送线程已取出正在发送的段：`clear_bulk` 不回撤；该段可能仍在途，属已声明限制。
- `batch_quiet_ms = 0`：每次 `flush_if_due` 都会立即 flush；仍受 `batch_max_chars` 上限。
- 输出 lane 溢出提示与正常输出同 lane，可能被后续溢出再次丢弃；控制 lane 不受影响。
- `OutboundBus.get` 在 `clear_bulk` 与 `put_bulk` 并发时由内部 Condition 串行化，不会取到半更新状态。

## 9. 错误处理

| 场景 | 处理 |
|---|---|
| 发送首次失败 | WARNING 日志，2s 后重试一次 |
| 重试仍失败 | ERROR 日志（原因 + 长度，不含正文），丢弃该段 |
| 队列溢出 | 丢弃最旧 + 按需补提示；WARNING 日志 |
| 控制 lane 超过软上限 | ERROR 日志，继续入队 |
| on_change 回调（持久化）抛异常 | 捕获并记 ERROR，不影响开关生效 |
| 单段发送未知异常 | 捕获并记 ERROR（含 traceback，不含正文），继续下一段 |

## 10. 日志要求

| 事件 | 级别 |
|---|---|
| mirror 开关变化 | INFO |
| generation 递增 | INFO（`output epoch: N`） |
| 输出 lane 溢出 | WARNING（含丢弃数） |
| 控制 lane 超过软上限 | ERROR（含长度） |
| 发送失败（首次/最终） | WARNING / ERROR |
| 回显抵消命中 | DEBUG（仅行长，不含正文） |
| 批处理 flush | DEBUG（段数、总长度、generation） |
| stale generation 丢弃 | DEBUG（仅 generation） |

## 11. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_normalize | 含空行、首尾空白、连续空格与换行 | 输出单空格折叠结果 |
| test_echo_register_match | 注册后整行命中 | suppress 删除该行并仅消费一次；二次 suppress 不命中 |
| test_echo_line_level | 块含回显行 + 两行回答 | 只删除回显行，回答保留 |
| test_echo_prefix | 行为 `> 测试消息`，注入 `测试消息` | 前缀匹配，删除该行 |
| test_echo_expiry | clock 前进 11s | suppress 不再命中，项被清除 |
| test_echo_fifo_two | 注册两条不同文本 | 只与队首比较；先匹配第一条 |
| test_echo_no_partial | 块与项部分相同 | 返回原行，不消费 |
| test_echo_max_entries | 注册 51 条 | 队列最多 50，最旧被丢 |
| test_filters_default | 纯空白行与正常行混合 | 空白行被丢弃，其余保留 |
| test_chunk_basic | 多行聚合 | 段内 `\n` 连接，总长 ≤ max_chars |
| test_chunk_exact | 单行恰 max_chars | 单独成段不硬切 |
| test_chunk_hard_split | 单行 2.5 × max_chars | 切成 3 段，长度 max/max/0.5max |
| test_chunk_flush_boundary | 加下一行即超限 | 先 flush 再开新段 |
| test_bus_control_priority | 同时有控制/输出 | get 先返回控制项 |
| test_bus_control_not_dropped | 输出 lane 容量 3 放 5 段后再放控制 | 控制项仍在，get 能取到 |
| test_bus_bulk_overflow | 容量 3 放 5 段 | 剩最新 3 段；含一条溢出提示且仅一条 |
| test_bus_notice_repeat | 提示被取走后再次溢出 | 再次追加一条提示 |
| test_bus_clear_bulk | clear_bulk 后 | 输出 lane 0，控制 lane 不变 |
| test_pipeline_mirror_off | 开关 off | 输入被丢弃，队列无变化 |
| test_pipeline_generation_stale | generation 不匹配 | 输入被丢弃，不 flush |
| test_pipeline_batch_quiet | 连续两轮释放，间隔小于 quiet | 不 flush；到期后 flush 一次 |
| test_pipeline_batch_max | 超过 batch_max_chars | 立即 flush |
| test_pipeline_order | 构造含回显与空白行的块 | 过滤先于回显；命中行不入队 |
| test_switch_persist | set(True/False) | on_change 每次都被调用，且在锁外 |
| test_epoch_monotonic | bump 两次 | 返回递增值，线程安全 |
| test_sender_retry | 首次失败二次成功 | 共调用 2 次，间隔约 2s，无 ERROR |
| test_sender_drop | 两次都失败 | ERROR 日志，继续处理下一段 |
| test_sender_stale | 取出后 epoch 变化 | 发送前丢弃，不调用 API |
| test_sender_interval | 连续 3 段输出 | 每段间隔 ≥ send_interval；控制段不被 1s 间隔拖住 |
