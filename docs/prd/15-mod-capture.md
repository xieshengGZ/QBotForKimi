# 15 · 模块规约：capture.py

> **2026-11-02 修订（ADR-0001）**：Step 0 真实 TUI 门禁发现 v1.1 的 `find_overlap` 主路径会在流式重绘时丢行。§4/§6 的差分与稳定器契约由 `docs/adr/0001-redraw-aware-capture.md` 取代；§3 的 capture 目标改为 `-t =<session>:`。其余章节仍有效。

## 1. 职责

输出通道的前半段：`capture-pane` 抓取、快照差分、稳定判定。本模块是无状态的算法核心（状态由调用方监控线程持有），不含 mirror、过滤、切段逻辑（属 mirror.py）。

v1.1 的变化：

- `find_overlap` 改为 KMP 线性算法，消除 O(n²) 最坏情况；
- 增加 `mask_tail`，用于屏蔽 TUI 底部易变行；
- 增加 `recover_new_lines`，在 `k=0` 时先用 `difflib` 恢复插入行，连续多次失败才重建基线。

## 2. 接口签名

```python
class CaptureError(Exception):
    """capture-pane 执行失败（tmux 不可用、会话不存在、超时等）。"""

VOLATILE_SENTINEL: str = "\x00__volatile__"

def capture_pane(session: str, lines: int, timeout: float = 5.0) -> list[str]:
    """抓取 session 活动 pane 的文本，返回行列表。失败/超时抛 CaptureError。"""

def mask_tail(lines: list[str], n: int) -> list[str]:
    """把最后 n 行替换为 VOLATILE_SENTINEL，用于差分时忽略底部易变行。
    n<=0 时返回浅拷贝；n>=len(lines) 时全部替换为 sentinel。"""

def find_overlap(prev: list[str], cur: list[str]) -> int:
    """返回最大的 k（k ≥ 0），使得 prev 的尾部 k 行与 cur 的头部 k 行完全相同。
    k = 0 表示无法匹配。实现使用 KMP，时间复杂度 O(len(prev)+len(cur))。"""

def recover_new_lines(prev: list[str], cur: list[str]) -> list[str]:
    """k=0 时的恢复路径：用 difflib.SequenceMatcher 找出 cur 中的纯插入块，
    返回这些插入行（按出现顺序）。没有插入块时返回 []。
    不返回 replace 块，避免把 TUI 状态栏的原地改写当成新输出。"""

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

执行（`subprocess.run`，list 形式，不经 shell，捕获 stdout/stderr，`timeout=5.0`）：

```
tmux capture-pane -p -J -S -<lines> -t =<session>
```

- `-p`：输出到 stdout；`-J`：合并软换行（长行不被 pane 宽度折成多行）；`-S -<lines>`：从滚动历史向上取 lines 行。
- 返回码非零或超时 → 抛 `CaptureError`（消息含 stderr 摘要或 timeout 标记，不含正文）。
- stdout 按行切分（`splitlines()`），不 strip、不过滤——空行保留（屏幕空白区域会呈现为空行，属正常，由下游 mirror 过滤）。
- 空输出 → 返回 `[]`。
- 使用 `text=True, errors="replace"`，避免非法 UTF-8 让监控线程崩溃。

## 4. find_overlap 行为规约（KMP）

定义：最大 k，使 `prev[-k:] == cur[:k]`（k 可至 `min(len(prev), len(cur))`）。

语义示例：

| 场景 | prev | cur | k | 新增行（cur[k:]） |
|---|---|---|---|---|
| 无滚动无变化 | `[A,B,C]` | `[A,B,C]` | 3 | `[]` |
| 追加 2 行 | `[A,B,C]` | `[A,B,C,D,E]` | 3 | `[D,E]` |
| 窗口下滑 2 行（追加 2 行，prev 取满 capture_lines） | `[A,B,C,D]` | `[C,D,E,F]` | 2 | `[E,F]` |
| 末行被原地改写 | `[A,B,C]` | `[A,B,C2]` | 0 | —（走 recover_new_lines） |
| 屏幕清空 | `[A,B,C]` | `[]` | 0 | —（走 recover_new_lines，通常无插入） |

伪代码：

```
def find_overlap(prev, cur):
    if not prev or not cur:
        return 0
    seq = cur + [VOLATILE_SENTINEL] + prev
    pi = [0] * len(seq)
    for i in range(1, len(seq)):
        j = pi[i - 1]
        while j > 0 and seq[i] != seq[j]:
            j = pi[j - 1]
        if seq[i] == seq[j]:
            j += 1
        pi[i] = j
    return min(pi[-1], len(prev), len(cur))
```

- 哨兵保证匹配不会越过 `cur` 的边界；`pi[-1]` 即“cur 的最长前缀 = prev 的后缀”。
- 不允许使用逐 k 全切片比较的暴力实现；5000 行 / 300ms 下必须有 O(n) 保证。
- `mask_tail` 在调用前处理：`find_overlap(mask_tail(prev, n), mask_tail(cur, n))`。被 mask 的尾部不参与重叠判断，但 `new_lines` 仍用原始 `cur[k:]`，因此底部状态行会进入候选块；若它持续变化，会被 StabilityTracker 的计数重置拦住，不会推送。

## 5. recover_new_lines 行为规约

```
def recover_new_lines(prev, cur):
    sm = difflib.SequenceMatcher(a=prev, b=cur, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            out.extend(cur[j1:j2])
    return out
```

- 只取 `insert`，不取 `replace`：TUI 状态栏原地改写通常表现为 replace，不应作为新输出推送。
- `prev`/`cur` 为空时返回 []。
- 该函数只作为 `k=0` 的恢复路径；正常路径仍用 `find_overlap`。

## 6. StabilityTracker 行为规约（含伪代码）

内部状态：`pending`，元素为 `(line, count)`——候选行与它已连续不变的轮数。

每轮调用 `feed(new_lines)`：

```
def feed(new_lines):
    # 1. 组块：旧候选行 + 本轮新增行
    block = [line for (line, _) in pending] + new_lines
    # 2. 计数：位置与内容都不变的行 count+1，否则重置为 1
    new_pending = []
    for i, line in enumerate(block):
        if i < len(pending) and pending[i][0] == line:
            new_pending.append((line, pending[i][1] + 1))
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
- `reset()`：清空 pending。
- 稳定释放只是“可以进入 mirror 管道”，不等于立即发送；mirror 的静默窗口批处理会进一步合并。

## 7. 调用方（监控线程）使用约定

监控线程持有 `prev: list[str] | None`、`tracker`、`k0_streak: int`：

```
每轮：
    cur = capture_pane(session, capture_lines)     # CaptureError → warning，跳过本轮
    if prev is None:                               # 启动 / #clear / respawn 后
        prev = cur; tracker.reset(); k0_streak = 0; continue
    pm = mask_tail(prev, volatile_tail_lines)
    cm = mask_tail(cur, volatile_tail_lines)
    k = find_overlap(pm, cm)
    if k == 0:
        recovered = recover_new_lines(prev, cur)
        if recovered:
            new_lines = recovered
            k0_streak = 0
        else:
            k0_streak += 1
            if k0_streak >= k0_rebuild_threshold:
                log warning（基线重建）
                prev = cur; tracker.reset(); k0_streak = 0
            stop_event.wait(poll)
            continue                                # 不更新 prev，下一轮再试
    else:
        k0_streak = 0
        new_lines = cur[k:]
    prev = cur
    released = tracker.feed(new_lines)
    → released 交给 mirror 管道；非空时更新"最后输出时间"
```

- 首次启动：第一轮只建基线，不把当前屏幕当作“新增”推送（不回放历史）。
- `#clear`：监控线程在下一轮开头发现 reset 请求 → `prev = 当前抓取`、`tracker.reset()`，本轮不产出。
- respawn/recreate 后：基线重置（`prev = None`），新进程输出从基线之后开始推送。
- `k=0` 不再立即丢弃片段：先 `recover_new_lines`；只有连续 `k0_rebuild_threshold` 次无恢复才重建基线。重建时记 warning，不视为错误。
- 快照中的空行参与重叠匹配与计数（不过滤），过滤是 mirror 的职责。

## 8. 错误处理

- `CaptureError`：监控线程记 warning 并跳过本轮（下一周期重试）。tmux server 消失时 pane 检测会先行返回 `MISSING`，capture 错误不触发恢复逻辑。
- 其他异常不外抛；监控线程外层还有逐轮 try/except 兜底。

## 9. 日志要求

| 事件 | 级别 |
|---|---|
| 基线重建（连续 k=0 达阈值） | WARNING（`capture diff: no overlap after N tries, baseline rebuilt`） |
| k=0 但 recover 成功 | INFO（仅计数，可每分钟汇总） |
| 基线初始化/重置 | DEBUG |
| 本轮新增/释放行数 | DEBUG（仅数量） |
| CaptureError / timeout | WARNING（stderr 摘要或 timeout） |
| mask_tail 生效 | DEBUG（仅数量） |

## 10. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_capture_command | mock subprocess | 命令参数与第 3 节一致（`-J`、`-S -5000`、`=session`、无 shell） |
| test_capture_timeout | mock 超时 | CaptureError，消息含 timeout |
| test_capture_parse | stdout 含空行与尾换行 | 行列表保留空行、无尾空串 |
| test_capture_error | 返回码非零 | CaptureError 含 stderr 摘要 |
| test_overlap_append | 第 4 节“追加 2 行” | k=3 |
| test_overlap_scroll | 第 4 节“窗口下滑” | k=2 |
| test_overlap_identical | prev == cur | k=len(prev) |
| test_overlap_rewrite | 末行改写 | k=0 |
| test_overlap_empty | prev 或 cur 为空 | k=0 |
| test_overlap_dup_lines | 含重复行的复杂样例 | 返回**最大** k；与暴力实现对比 |
| test_overlap_linear | 5000 行重复内容 | 在 50ms 内完成（本机基准，允许 5x 余量） |
| test_mask_tail | 末行不同 | mask 后 k 等于未 mask 的重叠；new_lines 仍含末行 |
| test_recover_insert | 中间插入两行 | 返回两行；不返回 replace 行 |
| test_recover_none | 屏幕清空 | 返回 [] |
| test_tracker_basic_release | 连续 3 轮相同候选（候选块以空行结尾，无尾行延迟） | 第 3 轮释放（stable_rounds=3） |
| test_tracker_tail_delay | 候选块末行非空 | 该行第 3 轮不释放、第 4 轮释放 |
| test_tracker_change_resets | 第 2 轮某行内容变化 | 该行计数重置为 1，前缀释放被截断 |
| test_tracker_prefix_only | 第 1 行不达标、第 2 行达标 | 本轮无释放 |
| test_tracker_feed_empty | 无新增时 feed([]) | 计数照常增长，到点释放 |
| test_tracker_reset | reset 后 feed | 计数从头开始 |
| test_monitor_k0_recover | 模拟 k=0 但 recover 有插入 | 不重建基线，插入行进入 tracker |
| test_monitor_k0_threshold | 连续 k=0 达阈值 | 重建基线，tracker.reset |
