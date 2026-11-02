# 15 · 模块规约：capture.py

## 1. 职责

输出通道的前半段：`capture-pane` 抓取、快照差分、稳定判定。本模块是无状态的算法核心（状态由调用方监控线程持有），不含 mirror、过滤、切段逻辑（属 mirror.py）。

## 2. 接口签名

```python
class CaptureError(Exception):
    """capture-pane 执行失败（tmux 不可用、会话不存在等）。"""

def capture_pane(session: str, lines: int) -> list[str]:
    """抓取 session 活动 pane 的文本，返回行列表。失败抛 CaptureError。"""

def find_overlap(prev: list[str], cur: list[str]) -> int:
    """返回最大的 k（k ≥ 0），使得 prev 的尾部 k 行与 cur 的头部 k 行完全相同。
    k = 0 表示无法匹配。"""

class StabilityTracker:
    """跟踪候选新增行，按轮次计数，释放达到稳定条件的行。
    非线程安全——仅由监控线程使用。"""
    def __init__(self, stable_rounds: int) -> None: ...
    def feed(self, new_lines: list[str]) -> list[str]:
        """输入本轮差分得到的新增行；返回本轮达到稳定条件、按序释放的行。"""
    def reset(self) -> None:
        """清空候选块（基线重建、#clear、respawn 时调用）。"""
```

## 3. capture_pane 行为规约

执行（`subprocess.run`，list 形式，不经 shell，捕获 stdout/stderr）：

```
tmux capture-pane -p -J -S -<lines> -t <session>
```

- `-p`：输出到 stdout；`-J`：合并软换行（长行不被 pane 宽度折成多行）；`-S -<lines>`：从滚动历史向上取 lines 行。
- 返回码非零 → 抛 `CaptureError`（消息含 stderr 摘要）。
- stdout 按行切分（`splitlines()`），不 strip、不过滤——空行保留（屏幕空白区域会呈现为空行，属正常，由下游 mirror 过滤）。
- 空输出 → 返回 `[]`。

## 4. find_overlap 行为规约（含伪代码）

定义：最大 k，使 `prev[-k:] == cur[:k]`（k 可至 `min(len(prev), len(cur))`）。

语义示例：

| 场景 | prev | cur | k | 新增行（cur[k:]） |
|---|---|---|---|---|
| 无滚动无变化 | `[A,B,C]` | `[A,B,C]` | 3 | `[]` |
| 追加 2 行 | `[A,B,C]` | `[A,B,C,D,E]` | 3 | `[D,E]` |
| 窗口下滑 2 行（追加 2 行，prev 取满 capture_lines） | `[A,B,C,D]` | `[C,D,E,F]` | 2 | `[E,F]` |
| 末行被原地改写 | `[A,B,C]` | `[A,B,C2]` | 0 | —（无法匹配） |
| 屏幕清空 | `[A,B,C]` | `[]` | 0 | —（无法匹配） |

伪代码（按候选位置剪枝，避免 O(n²) 全量比较）：

```
def find_overlap(prev, cur):
    maxk = min(len(prev), len(cur))
    if maxk == 0:
        return 0
    # 快路径：完全前缀重合（无滚动场景最常见）
    if prev == cur[:len(prev)]:
        return len(prev)
    # 候选 k：cur 的第 k-1 行必须等于 prev 的最后一行
    last = prev[-1]
    k = maxk
    while k >= 1:
        if cur[k - 1] == last and prev[-k:] == cur[:k]:
            return k
        k -= 1
    return 0
```

正确性以定义为准；实现可采用任何等价算法，但须避免逐 k 全切片比较的暴力实现（capture_lines 可达 5000，轮询周期 300ms）。

## 5. StabilityTracker 行为规约（含伪代码）

内部状态：`pending`，元素为 `(line, count)`——候选行与它已连续不变的轮数。

每轮调用 `feed(new_lines)`：

```
def feed(new_lines):
    # 1. 组块：旧候选行 + 本轮新增行
    block = [line for (line, _) in pending] + new_lines
    # 2. 计数：位置与内容都不变的行 count+1，否则重置为 1
    new_pending = []
    for i, line in enumerate(block):
        if i < len(pending) and pending[i].line == line:
            new_pending.append((line, pending[i].count + 1))
        else:
            new_pending.append((line, 1))
    # 3. 找块内最后一行非空行下标 L（全空或无行时 L = -1）
    L = max((i for i, (line, _) in enumerate(new_pending) if line.strip() != ""),
            default=-1)
    # 4. 释放前缀：第 i 行所需轮数 req(i)
    #    i == L → stable_rounds + 1；其余 → stable_rounds
    cut = 0
    for i, (line, c) in enumerate(new_pending):
        req = stable_rounds + 1 if i == L else stable_rounds
        if c >= req:
            cut = i + 1
        else:
            break
    released = [line for (line, _) in new_pending[:cut]]
    pending = new_pending[cut:]
    return released
```

要点：

- **尾行延迟**：块内最后一行非空行需 `stable_rounds + 1` 轮（默认 4 轮）不变才放行，防止把进度条等中间态推出去；其上方各行按正常 `stable_rounds`（默认 3）判定。
- 释放是**前缀语义**：某行未达标时，其后的行本轮一律不释放（保持输出顺序）。
- 调用方在无新增行的轮次也要 `feed([])`，候选行的计数才能增长。
- `reset()`：清空 pending，返回值无。

## 6. 调用方（监控线程）使用约定

监控线程持有 `prev: list[str] | None` 与 `tracker`：

```
每轮：
    cur = capture_pane(session, capture_lines)     # CaptureError → 记 warning，跳过本轮
    if prev is None:                               # 启动 / #clear / respawn 后
        prev = cur; tracker.reset(); continue      # 建立基线，本轮不产生新增
    k = find_overlap(prev, cur)
    if k == 0:                                     # 无法匹配
        log warning
        prev = cur; tracker.reset(); continue      # 重建基线并丢弃该片段
    new_lines = cur[k:]
    prev = cur
    released = tracker.feed(new_lines)
    → released 交给 mirror 管道；非空时更新"最后输出时间"
```

## 7. 边界条件

- 首次启动：第一轮只建基线，不把当前屏幕当作"新增"推送（不回放历史）。
- `#clear`：监控线程在下一轮开头发现 reset 请求 → `prev = 当前抓取`、`tracker.reset()`，本轮不产出（见 `18-mod-main.md`）。
- respawn 后：基线重置（`prev = None`），新进程输出从基线之后开始推送（见 `17-mod-session.md` 与 `18-mod-main.md`）。
- 无法匹配（k=0）属预期内事件（屏幕清空、末行原地改写等），记 warning 但不视为错误，不中断轮询。
- 快照中的空行参与重叠匹配与计数（不过滤），过滤是 mirror 的职责。

## 8. 错误处理

- `CaptureError`：监控线程记 warning 并跳过本轮（下一周期重试）。tmux server 消失时 pane 检测会先行失败（见 `17-mod-session.md`），capture 错误不触发恢复逻辑。
- 其他异常不外抛。

## 9. 日志要求

| 事件 | 级别 |
|---|---|
| 基线重建（k=0） | WARNING（`capture diff: no overlap, baseline rebuilt`） |
| 基线初始化/重置 | DEBUG |
| 本轮新增/释放行数 | DEBUG（仅数量） |
| CaptureError | WARNING（stderr 摘要） |

## 10. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_capture_command | mock subprocess | 命令参数与第 3 节一致（`-J`、`-S -5000`、无 shell） |
| test_capture_parse | stdout 含空行与尾换行 | 行列表保留空行、无尾空串 |
| test_capture_error | 返回码非零 | CaptureError 含 stderr 摘要 |
| test_overlap_append | 第 4 节"追加 2 行" | k=3 |
| test_overlap_scroll | 第 4 节"窗口下滑" | k=2 |
| test_overlap_identical | prev == cur | k=len(prev) |
| test_overlap_rewrite | 末行改写 | k=0 |
| test_overlap_empty | prev 或 cur 为空 | k=0 |
| test_overlap_dup_lines | 含重复行的复杂样例 | 返回**最大** k（用人工构造用例验证） |
| test_tracker_basic_release | 连续 3 轮相同候选（候选块以空行结尾，无尾行延迟） | 第 3 轮释放（stable_rounds=3） |
| test_tracker_tail_delay | 候选块末行非空 | 该行第 3 轮不释放、第 4 轮释放 |
| test_tracker_change_resets | 第 2 轮某行内容变化 | 该行计数重置为 1，前缀释放被截断 |
| test_tracker_prefix_only | 第 1 行不达标、第 2 行达标 | 本轮无释放 |
| test_tracker_feed_empty | 无新增时 feed([]) | 计数照常增长，到点释放 |
| test_tracker_reset | reset 后 feed | 计数从头开始 |
| test_monitor_convention | 模拟三轮快照（含 k=0 重建） | 重建轮不产出、prev 更新为当前快照 |
