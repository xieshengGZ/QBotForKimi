# 16 · 模块规约：mirror.py

## 1. 职责

输出通道的后半段：mirror 开关管理、行过滤、回显抵消、切段、发送队列、限速发送。接收 capture 释放的稳定行，产出进入发送队列的文本段；发送线程把段投递到 NapCat。

## 2. 接口签名

```python
class MirrorSwitch:
    """mirror 开关，线程安全。on_change 回调用于持久化（__main__ 接线到 StateStore.save）。"""
    def __init__(self, initial: bool, on_change: Callable[[bool], None] | None = None) -> None: ...
    def is_on(self) -> bool: ...
    def set(self, value: bool) -> None:
        """更新并调用 on_change(value)。重复设置同值也触发回调（幂等持久化）。"""

def normalize(text: str) -> str:
    """归一化：按行切分 → 逐行 strip → 丢弃空行 → 以空格连接 →
    折叠所有连续空白为单个空格 → strip。用于回显比对。"""

class EchoSuppressor:
    """回显待抵消队列（FIFO）。线程安全。"""
    def __init__(self, window_seconds: float = 10.0,
                 clock: Callable[[], float] = time.monotonic) -> None: ...
    def register(self, text: str) -> None:
        """登记一条注入文本的回显，窗口 window_seconds 内有效。"""
    def match(self, lines: list[str]) -> bool:
        """先清除过期项；若队首项与 normalize(\"\\n\".join(lines)) 相等，
        消费该项并返回 True（整块跳过，只抵消一次）；否则返回 False。"""

def passes_filters(line: str, patterns: list[re.Pattern]) -> bool:
    """line 不匹配任何 pattern 时返回 True（放行）。"""

def compile_patterns(patterns: list[str]) -> list[re.Pattern]: ...

def chunk_lines(lines: list[str], max_chars: int) -> list[str]:
    """把行列表切成若干发送段（规则见第 4 节）。"""

class OutboundQueue:
    """发送队列。线程安全。"""
    def __init__(self, maxlen: int, overflow_notice: str) -> None: ...
    def put(self, chunk: str) -> None:
        """入队；超容量时丢弃最旧，必要时补一条溢出提示（见第 5 节）。"""
    def get(self, timeout: float = 0.5) -> str | None: ...
    def clear(self) -> None:
        """清空（#mirror off / #clear）。同时清除溢出提示状态。"""
    def qsize(self) -> int: ...

class MirrorPipeline:
    """稳定行处理管道：开关 → 过滤 → 回显 → 切段 → 入队。仅监控线程调用。"""
    def __init__(self, switch: MirrorSwitch, echo: EchoSuppressor,
                 queue: OutboundQueue, patterns: list[re.Pattern],
                 chunk_chars: int) -> None: ...
    def handle_stable_lines(self, lines: list[str]) -> None: ...

def run_sender_loop(
    queue: OutboundQueue,
    api_base: str,
    access_token: str,
    user_id: int,
    send_interval: float,          # 秒
    stop_event: threading.Event,
) -> None:
    """发送线程主循环；stop_event 置位后退出。"""
```

## 3. 开关语义

- `mirror=off`：`handle_stable_lines` **直接丢弃**输入，不做过滤/回显/切段（基线已在 capture 侧推进，重新开启后不回放旧内容）。
- `#mirror off`：input worker 先 `set(False)` 再 `OutboundQueue.clear()`（清空待发送队列），回执随后入队。
- mirror 状态持久化：__main__ 将 `MirrorSwitch.on_change` 接到 `StateStore.save`；启动时以 `StateStore.load()` 为初始值。

## 4. 切段规则（chunk_lines）

按整行聚合，尽量不拆行；超长单行硬切：

```
def chunk_lines(lines, max_chars):
    chunks, cur = [], ""
    for line in lines:
        # 单行超过 max_chars：先 flush 当前段，再硬切成等长多段
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

## 5. 发送队列溢出规则

`put` 时若 `qsize() >= maxlen`：循环丢弃队首直到有空位再放入新段；每次 `put` 发生丢弃后，若队列中**当前不存在**未发送的溢出提示（`overflow_notice` 文本），则在队尾追加一条。判定"存在"以队列内容扫描为准；提示被取走发送后，再次发生溢出时可再次追加。

`clear()` 清空全部内容（含未发送的溢出提示）。

## 6. 发送循环规则（run_sender_loop）

```
while not stop_event.is_set():
    chunk = queue.get(timeout=0.5)
    if chunk is None:
        continue
    try:
        send_private_msg(api_base, access_token, user_id, chunk)   # 超时 3s
    except OnebotAPIError:
        log warning（首败重试）
        time.sleep(2)
        try:
            send_private_msg(...)
        except OnebotAPIError as e:
            log error（放弃该段，含原因摘要与段的前 80 字符）
    stop_event.wait(send_interval)   # 段间间隔，默认可被 stop 唤醒
```

- 每处理完一段（成功或最终失败）等待 `send_interval` 秒（默认 1s）。
- 不重试第二次以上；不阻塞后续段。
- 停机：`get` 超时轮询 + `stop_event.wait` 保证 1s 内可退出；队列中未发送内容丢弃，不持久化。

## 7. 过滤与回显细则

- `ignore_patterns` 默认 `["^\\s*$"]`（丢弃纯空白行）。逐行判定，命中的行直接丢弃。
- 处理顺序：mirror 开关 → 行过滤 → 若剩余为空则结束 → 回显匹配（整块）→ 切段入队。
- 回显匹配以**过滤后的整块行列表**为单位：与队首待抵消项比较，相等则整块跳过并消费该项；不相等则放行，**不**消费（等待后续块或过期）。
- `normalize` 两侧一致：注入文本与候选块都先归一化再比较，兼容终端折行与空白差异。
- 窗口从 `register` 调用时刻起算 10 秒；过期项在下一次 `match` 时清除。

## 8. 边界条件

- 单行恰好 `max_chars`：不硬切；与前行拼接超长时单独成段。
- 单行 `max_chars + 1`：硬切成 `max_chars` + `1` 两段。
- 回显项过期未被命中：自动清除，不影响后续输出。
- 连续注入两条相同文本：登记两项；第一个命中块消费第一项，第二个命中块消费第二项。
- 发送线程在 `clear()` 时已取出正在发送的段：继续发送完毕（不回撤）。
- `send_interval = 0`：不等待，连续发送。

## 9. 错误处理

| 场景 | 处理 |
|---|---|
| 发送首次失败 | WARNING 日志，2s 后重试一次 |
| 重试仍失败 | ERROR 日志（原因 + 段前 80 字符），丢弃该段 |
| 队列溢出 | 丢弃最旧 + 按需补提示；WARNING 日志 |
| on_change 回调（持久化）抛异常 | 捕获并记 ERROR，不影响开关生效 |

## 10. 日志要求

| 事件 | 级别 |
|---|---|
| mirror 开关变化 | INFO |
| 发送队列溢出 | WARNING |
| 发送失败（首次/最终） | WARNING / ERROR |
| 回显抵消命中 | DEBUG |
| 切段入队 | DEBUG（段数与总长度） |

## 11. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_normalize | 含空行、首尾空白、连续空格与换行 | 输出单空格折叠结果 |
| test_echo_register_match | 注册后整块命中 | match 返回 True 且仅消费一次（二次 match 返回 False） |
| test_echo_expiry | clock 前进 11s | match 不再命中，项被清除 |
| test_echo_fifo_two | 注册两条不同文本 | 只与队首比较；先匹配第一条 |
| test_echo_no_partial | 块与项部分相同 | 返回 False 且不消费 |
| test_filters_default | 纯空白行与正常行混合 | 空白行被丢弃，其余保留 |
| test_chunk_basic | 多行聚合 | 段内 `\n` 连接，总长 ≤ max_chars |
| test_chunk_exact | 单行恰 max_chars | 单独成段不硬切 |
| test_chunk_hard_split | 单行 2.5 × max_chars | 切成 3 段，长度 max/max/0.5max |
| test_chunk_flush_boundary | 加下一行即超限 | 先 flush 再开新段 |
| test_queue_overflow | 容量 3 放 5 段 | 剩最新 3 段；含一条溢出提示且仅一条 |
| test_queue_notice_repeat | 提示被取走后再次溢出 | 再次追加一条提示 |
| test_queue_clear | clear 后 | qsize=0，提示状态重置 |
| test_pipeline_mirror_off | 开关 off | 输入被丢弃，队列无变化 |
| test_pipeline_order | 构造含回显与空白行的块 | 过滤先于回显；命中块不入队 |
| test_switch_persist | set(True/False) | on_change 每次都被调用 |
| test_sender_retry | 首次失败二次成功 | 共调用 2 次，间隔约 2s，无 ERROR |
| test_sender_drop | 两次都失败 | ERROR 日志，继续处理下一段 |
| test_sender_interval | 连续 3 段 | 每段间隔 ≥ send_interval |
