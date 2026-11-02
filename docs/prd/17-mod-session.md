# 17 · 模块规约：session.py

## 1. 职责

tmux 会话生命周期：创建/接管会话、pane 状态检测、进程退出后的退避恢复与告警。tmux server 与 kimico 独立于网关进程存活。

v1.1 的关键变化：pane 状态从 `bool is_pane_dead` 改为四态，只有确认 `DEAD`/`MISSING` 才恢复；`UNKNOWN` 不允许 respawn，避免 tmux 瞬时故障误杀活进程。

## 2. 接口签名

```python
class TmuxError(Exception):
    """tmux 命令执行失败。message 含命令与 stderr 摘要（不含注入文本）。"""

class PaneState(Enum):
    ALIVE = auto()     # pane_dead=0
    DEAD = auto()      # pane_dead=1，会话存在
    MISSING = auto()   # 会话或 tmux server 不存在
    UNKNOWN = auto()   # 命令超时/失败/输出无法解析

def session_exists(session: str) -> bool:
    """tmux has-session -t =<session> 判定；返回码 0 即存在。
    超时或 OSError 抛 TmuxError，不返回 False。"""

def ensure_session(session: str, kimico_command: str, history_limit: int) -> None:
    """幂等：会话存在则设置/校验 remain-on-exit 后返回；不存在则先设 history-limit，
    再创建会话并设置 remain-on-exit。创建失败抛 TmuxError。"""

def get_pane_state(session: str) -> PaneState:
    """四态检测。has-session 返回非零 → MISSING；has-session 超时/OSError → UNKNOWN；
    display-message 非零/超时 → UNKNOWN；stdout '0' → ALIVE；'1' → DEAD；其他 → UNKNOWN。"""

def respawn_pane(session: str, kimico_command: str) -> None:
    """仅在确认 DEAD 后调用；在死 pane 上原地重启 kimico。失败抛 TmuxError。"""

class RecoveryManager:
    """退避恢复状态机。仅由监控线程使用，非线程安全。
    notify 用于发送控制 lane 通知（__main__ 接线到 OutboundBus.put_control，不抛异常）。"""
    def __init__(
        self,
        cfg: RecoveryConfig,
        notify: Callable[[str], None],
        respawn: Callable[[], None],       # DEAD 时调用，如 partial(respawn_pane, ...)
        recreate: Callable[[], None],      # MISSING 时调用，如 partial(ensure_session, ...)
        pane_state: Callable[[], PaneState],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None: ...
    def note_alive(self) -> None:
        """监控线程每轮检测到 ALIVE 时调用。连续存活满
        success_reset_seconds 后清零失败计数（退避档位随之归零）。"""
    def note_dead_and_recover(self, state: PaneState) -> bool:
        """state 为 DEAD 或 MISSING 时调用；UNKNOWN 不得调用。
        执行一次恢复流程（可能阻塞 sleep）。
        返回 True 表示本轮执行了 respawn/recreate 且命令成功（调用方应重置输出基线）；
        返回 False 表示未执行（已熔断、恢复失败、或恢复后 pane 已活）。"""
    @property
    def halted(self) -> bool:
        """熔断后返回 True：窗口内失败数达上限，停止恢复直到进程重启。"""
```

## 3. tmux 命令约定

全部用 `subprocess.run`（list 形式，不经 shell），并带 `timeout=5`；超时杀进程组并抛 `TmuxError`。

| 操作 | 命令 |
|---|---|
| 会话存在 | `tmux has-session -t =<session>`（返回码 0 即存在） |
| 创建会话 | `tmux new-session -d -s <session> -- <argv...>` |
| 设置滚动历史 | `tmux set-option -g history-limit <history_limit>`（必须在 new-session 之前） |
| 设置死 pane 保留 | `tmux set-option -w -t =<session> remain-on-exit on` |
| 校验 remain-on-exit | `tmux show-options -wv -t =<session> remain-on-exit` |
| 校验 history-limit | `tmux show-options -gv history-limit` |
| pane 状态检测 | `tmux display-message -p -t =<session> '#{pane_dead}'` → `0` / `1` |
| 原地重启 | `tmux respawn-pane -k -t =<session> -- <argv...>`（仅 DEAD） |

- `ensure_session` 创建路径：`set-option -g history-limit` → `new-session` → `set-option -w remain-on-exit` → 校验。任一步失败抛 `TmuxError`。
- `ensure_session` 接管路径（会话已存在）：幂等执行 `set-option -w remain-on-exit on` 并校验；**不改** `history-limit`（已有 pane 无法扩容），若实际值小于配置值记 WARNING 并提示长输出可能截断。
- `kimico_command` 用 `shlex.split` 展开为 argv；第一项在启动时由 `__main__` 预检可执行。命令中不支持 shell 操作符（`&&`、`|`、重定向）。
- `get_pane_state` 的 `MISSING` 只在 `has-session` 返回非零时产生；`has-session` 超时/OSError、`display-message` 失败都是 `UNKNOWN`，不是 `DEAD` 或 `MISSING`。
- `respawn_pane` 只允许在 `get_pane_state() == DEAD` 后调用；`-k` 只用于已确认死亡的 pane。

## 4. 恢复行为规约（RecoveryManager 状态机）

内部状态：`failures`（失败时间戳列表）、`alive_since`（本次连续存活起点）、`pending_attempt`（已发出 respawn/recreate、等待下一轮结算）、`halted`、`alarmed`。

### note_alive（pane 存活的每一轮）

1. `alive_since` 为 None 时设为 `clock()`。
2. 若 `failures` 非空且 `clock() - alive_since >= success_reset_seconds`：清空 `failures`，记 INFO。

### note_dead_and_recover（DEAD/MISSING 的每一轮）

1. 若 `halted`：直接返回 False。
2. **结算上一次尝试**：若 `pending_attempt` 为真：
   - `alive_since` 非 None 且 `now - alive_since >= success_reset_seconds` → 清空 `failures`（上一次恢复成功且运行达标）；
   - 否则 → `failures.append(now)`（上一次恢复失败或存活不足）；`pending_attempt=False`，`alive_since=None`。
3. **结算本次死亡**：若 `pending_attempt` 为假：
   - `alive_since` 非 None：
     - 存活达标 → 清空 `failures`；
     - 存活不足 → `failures.append(now)`（运行中的进程过早死亡）；
   - `alive_since` 为 None：首次观察即 dead，**不记失败**；
   - `alive_since=None`。
4. 清理 `failures` 中早于 `now - window_seconds` 的时间戳。
5. 若 `len(failures) >= max_attempts`：置 `halted`；若未告警过 → `notify(告警文本)` 并记 ERROR，置 `alarmed`；返回 False。
6. `attempt = len(failures) + 1`；`wait = backoff_seconds[min(len(failures), len(backoff_seconds) - 1)]`；`notify(重启通知文本)`（每次尝试都通知）。
7. `sleep(wait)`（生产传入 `stop_event.wait`，可被停机打断）。
8. 再检查：若 `pane_state() == ALIVE`（外部干预复活）→ `alive_since = clock()`，`pending_attempt=False`，返回 False。
9. 按状态执行：
   - `DEAD` → `respawn()`；
   - `MISSING` → `recreate()`；
   - 抛 `TmuxError` → `failures.append(clock())`，`pending_attempt=False`，记 ERROR，返回 False。
   - 成功 → `alive_since = clock()`，`pending_attempt=True`，记 INFO，返回 True。

### 与配置参数的对应（默认值示例）

- backoff `[5,15,60]`、max 3：第 1 次尝试等 5s；respawn 后 60s 内又死 → 记失败 1，第 2 次等 15s；再死 → 记失败 2，第 3 次等 60s；再死 → 记失败 3，窗口内累计 3 → 熔断告警。
- respawn 命令本身失败：立即记一次失败（步骤 9），下一轮步骤 2 因 `pending_attempt=False` 不会重复记；下一轮直接按新档位退避。
- 启动时 pane 已是 DEAD：`alive_since=None`、`pending_attempt=False`，步骤 3 不记失败，第一次等待 5s。
- 进程连续运行满 60s：计数与档位清零——之后再次崩溃从 5s 档重新开始。
- 网关重启：计数器在内存中，自然清零。

### 通知文本

由 `router.py` 文案模板渲染（见 `13-mod-router.md`）：

- 重启通知：`RESTART_NOTICE_TEMPLATE` 填 `wait`、`attempt`、`max`。
- 告警：`ALARM_TEXT_TEMPLATE` 填 `window`、`max`（窗口内累计失败达上限）。

## 5. 边界条件

- tmux server 整体消失（kill-server）：`has-session` 非零 → `MISSING` → `recreate` 尝试重建；重建失败计入失败；3 次后熔断告警。
- pane dead 但网关重启过：启动时 `ensure_session` 接管已存在的死 pane 会话，监控线程首次检测为 DEAD，进入恢复流程；首次不计失败，等待 5s。
- 恢复期间（sleep 阻塞中）收到 SIGTERM：`stop_event.wait` 立即返回，监控线程随主流程退出。
- 熔断后 kimico 保持 dead；`#status` 中 `kimico 进程：已退出`；用户可手动处理，或重启网关清零重试。
- `get_pane_state()` 返回 `UNKNOWN` 时，监控线程只记 warning 并跳过，不得调用 `note_dead_and_recover`。
- `ensure_session` 接管路径发现会话结构异常（0 个 pane、多个 window）→ 启动失败并给出明确错误，不进入不确定状态。

## 6. 错误处理

- 所有 tmux 失败归一为 `TmuxError`；恢复流程内部消化（计入失败），不向监控线程抛出。
- `notify` 回调不允许抛异常（__main__ 注入的实现须吞掉异常，记日志）。
- `subprocess` 超时按 `TmuxError` 处理；不得裸抛 `TimeoutExpired`。

## 7. 日志要求

| 事件 | 级别 |
|---|---|
| 会话创建/接管 | INFO（含 history-limit/remain-on-exit 校验结果） |
| 检测到 pane DEAD/MISSING | WARNING（含当前失败计数、状态） |
| 检测到 pane UNKNOWN | WARNING（含命令摘要，不 respawn） |
| 恢复尝试（档位、等待秒数、DEAD/MISSING） | INFO |
| respawn/recreate 成功 | INFO |
| respawn/recreate 失败 | ERROR |
| 计数清零（存活达标） | INFO |
| 熔断与告警 | ERROR |

## 8. 测试清单

全部 tmux 调用 mock subprocess；时间经注入的 `clock`/`sleep` 控制。

| 测试 | 场景 | 断言 |
|---|---|---|
| test_ensure_history_before_new | 会话不存在 | `set-option -g history-limit` 在 `new-session` 之前 |
| test_ensure_takeover_sets_remain | 会话已存在 | 执行 `set-option -w remain-on-exit on` 并校验；不执行 new-session |
| test_ensure_create_fail | new-session 返回码 1 | 抛 TmuxError |
| test_ensure_low_history_warn | 接管时 history-limit 低于配置 | WARNING，不重建 |
| test_pane_state_alive | stdout `0` | ALIVE |
| test_pane_state_dead | stdout `1` | DEAD |
| test_pane_state_missing | has-session 返回非零 | MISSING |
| test_pane_state_unknown | display-message 超时/非 0/非 0-1 | UNKNOWN |
| test_respawn_command | mock | `respawn-pane -k -t =s -- <argv...>` |
| test_recovery_first_death | 存活很久后死亡 | 不计失败；通知第 1 次（5s）；sleep(5)；respawn 成功返回 True |
| test_recovery_startup_dead | 启动即 DEAD | 不计失败；第 1 次等待 5s |
| test_recovery_early_death | respawn 后 30s 又死 | 记失败 1；通知第 2 次（15s） |
| test_recovery_respawn_fail_once | respawn 抛 TmuxError | 只记 1 次失败；下一轮不重复记 |
| test_recovery_halt | 连续 3 次死亡均不足 60s | 第 4 次死亡时熔断：不再 respawn，告警一次，halted=True |
| test_recovery_missing_recreate | MISSING | 调用 recreate，不调用 respawn |
| test_recovery_unknown_ignored | 监控层收到 UNKNOWN | 不调用 note_dead_and_recover（接线测试） |
| test_recovery_reset_after_60s | respawn 后存活 61s 再死 | 计数清零，重新从第 1 档 5s 开始 |
| test_recovery_window_slide | 失败时间戳超出 window | 旧失败被剔除，不熔断 |
| test_recovery_alarm_once | 熔断后继续死 | 告警只发一次 |
| test_recovery_revive_externally | sleep 后 pane 已 ALIVE | 跳过 respawn，返回 False |
| test_recovery_notify_text | 检查通知文本 | 与模板渲染结果一致（`第 2/3 次`、`15s`） |
