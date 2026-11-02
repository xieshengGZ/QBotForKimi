# 17 · 模块规约：session.py

## 1. 职责

tmux 会话生命周期：创建/接管会话、pane 存活检测、进程退出后的退避恢复与告警。tmux server 与 kimico 独立于网关进程存活。

## 2. 接口签名

```python
class TmuxError(Exception):
    """tmux 命令执行失败。message 含命令与 stderr 摘要。"""

def session_exists(session: str) -> bool:
    """tmux has-session -t <session> 判定。tmux server 不存在时返回 False。"""

def ensure_session(session: str, kimico_command: str, history_limit: int) -> None:
    """幂等：会话存在则直接返回（接管）；不存在则创建并配置（见第 3 节）。
    创建失败抛 TmuxError。"""

def is_pane_dead(session: str) -> bool:
    """检测 pane 进程是否已退出。会话不存在或命令失败按 dead 处理（返回 True）。"""

def respawn_pane(session: str, kimico_command: str) -> None:
    """在死 pane 上原地重启 kimico。失败抛 TmuxError。"""

class RecoveryManager:
    """退避恢复状态机。仅由监控线程使用，非线程安全。
    notify 用于发送 QQ 通知（__main__ 接线到 OutboundQueue.put，不抛异常）。"""
    def __init__(
        self,
        cfg: RecoveryConfig,                 # config.RecoveryConfig
        notify: Callable[[str], None],
        respawn: Callable[[], None],         # 注入，如 partial(respawn_pane, session, kimico_command)
        is_alive: Callable[[], bool],        # 注入，如 lambda: not is_pane_dead(session)
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None: ...
    def note_alive(self) -> None:
        """监控线程每轮检测到 pane 存活时调用。连续存活满
        success_reset_seconds 后清零失败计数（退避档位随之归零）。"""
    def note_dead_and_recover(self) -> bool:
        """检测到 pane dead 时调用。执行一次恢复流程（可能阻塞 sleep）。
        返回 True 表示本轮执行了 respawn 且命令成功（调用方应重置输出基线）；
        返回 False 表示未 respawn（已熔断、或 respawn 失败、或恢复后 pane 已活）。"""
    @property
    def halted(self) -> bool:
        """熔断后返回 True：窗口内失败数达上限，停止恢复直到进程重启。"""
```

## 3. tmux 命令约定

全部用 `subprocess.run`（list 形式，不经 shell）执行：

| 操作 | 命令 |
|---|---|
| 会话存在 | `tmux has-session -t <session>`（返回码 0 即存在） |
| 创建会话 | `tmux new-session -d -s <session> <kimico_command>` |
| 设置滚动历史 | `tmux set-option -g history-limit <history_limit>` |
| 设置死 pane 保留 | `tmux set-option -w -t <session> remain-on-exit on` |
| pane 存活检测 | `tmux display-message -p -t <session> '#{pane_dead}'` → stdout 为 `0`（活）/`1`（死） |
| 原地重启 | `tmux respawn-pane -k -t <session> <kimico_command>` |

- `ensure_session` 创建路径：new-session → set-option -g history-limit → set-option -w remain-on-exit。任一步失败抛 `TmuxError`。
- `ensure_session` 接管路径（会话已存在）：不改写任何配置，直接返回。
- `is_pane_dead`：命令返回码非零（含会话不存在）→ 返回 True；stdout 非 `0`/`1` 也按 True 处理。
- kimico 直接作为 pane 启动命令运行，不经过交互式 shell；`kimico_command` 若含参数，按 shell 分词（`shlex.split`）展开为 argv 传给 tmux。

## 4. 恢复行为规约（RecoveryManager 状态机）

内部状态：`failures`（失败时间戳列表）、`alive_since`（本次连续存活起点，pane 死时置 None）、`halted`（熔断标志，仅进程重启可清除）、`alarmed`（告警已发标志）。

### note_alive（pane 存活的每一轮）

1. `alive_since` 为 None 时设为 `clock()`。
2. 若 `failures` 非空且 `clock() - alive_since >= success_reset_seconds`：清空 `failures`（连续运行达标，计数与退避档位清零），记 INFO。

### note_dead_and_recover（pane dead 的每一轮）

1. 若 `halted`：直接返回 False。
2. **成败结算**：若 `alive_since` 非 None 且 `clock() - alive_since >= success_reset_seconds` → 清空 `failures`（上一轮进程运行达标，此前的失败一笔勾销）；否则（存活不足阈值或尚未记录存活）→ 把当前时间记入 `failures`（上一次恢复尝试失败；首次死亡时因通常满足存活达标而清零，不记失败）。
3. 清理 `failures` 中早于 `clock() - window_seconds` 的时间戳。
4. 若 `len(failures) >= max_attempts`：置 `halted`；若未告警过 → `notify(告警文本)` 并记 ERROR，置 `alarmed`；返回 False。
5. `attempt = len(failures) + 1`；`wait = backoff_seconds[min(len(failures), len(backoff_seconds) - 1)]`；`notify(重启通知文本)`（每次尝试都通知）。
6. `sleep(wait)`。
7. 再检查：若 `is_alive()` 为真（外部干预复活）→ `alive_since = clock()`，返回 False。
8. 调用注入的 `respawn()`：不抛异常视为成功 → `alive_since = clock()`，记 INFO，返回 True；抛 `TmuxError` → 把当前时间记入 `failures`，记 ERROR，返回 False（下一轮从第 3 步继续，退避档位自然 +1）。

### 与配置参数的对应（默认值示例）

- backoff `[5,15,60]`、max 3：第 1 次尝试等 5s；若 respawn 后 60s 内又死 → 记失败 1，第 2 次尝试等 15s；再死 → 记失败 2，第 3 次尝试等 60s；再死 → 记失败 3，窗口内累计 3 → 熔断告警。
- respawn 命令本身失败：立即记一次失败（不等存活观察），下一轮直接按新档位退避。
- 进程连续运行满 60s：计数与档位清零——之后再次崩溃从 5s 档重新开始。
- 网关重启：计数器在内存中，自然清零（"网关重启后计数清零"）。

### 通知文本

由 `router.py` 文案模板渲染（见 `13-mod-router.md`）：

- 重启通知：`RESTART_NOTICE_TEMPLATE` 填 `wait`、`attempt`、`max`。
- 告警：`ALARM_TEXT_TEMPLATE` 填 `max`。

## 5. 边界条件

- tmux server 整体消失（kill-server）：`has-session`/`display-message` 失败 → 视为 dead；`respawn-pane` 失败 → 计入失败；3 次后熔断告警。**不**在恢复路径重建会话（会话创建只在网关启动自检 `ensure_session`）。
- pane dead 但网关重启过：网关启动时 `ensure_session` 接管已存在的死 pane 会话，监控线程的首次 dead 检测即进入恢复流程。
- 恢复期间（sleep 阻塞中）收到 SIGTERM：监控线程随主流程退出（sleep 注入版在测试中可打断；生产实现用 `stop_event.wait(wait)` 代替裸 `time.sleep`，使退避可被打断——默认参数 `sleep` 由 `__main__` 传入 `stop_event.wait`）。
- 熔断后 kimico 保持 dead；`#status` 中 `kimico 进程：已退出`；用户可在机器上手动处理，或重启网关清零重试。

## 6. 错误处理

- 所有 tmux 失败归一为 `TmuxError`；恢复流程内部消化（计入失败），不向监控线程抛出。
- `notify` 回调不允许抛异常（__main__ 注入的实现须吞掉异常，记日志）。

## 7. 日志要求

| 事件 | 级别 |
|---|---|
| 会话创建/接管 | INFO |
| 检测到 pane dead | WARNING（含当前失败计数） |
| 恢复尝试（档位、等待秒数） | INFO |
| respawn 成功 | INFO |
| respawn 失败 | ERROR |
| 计数清零（存活达标） | INFO |
| 熔断与告警 | ERROR |

## 8. 测试清单

全部 tmux 调用 mock subprocess；时间经注入的 `clock`/`sleep` 控制。

| 测试 | 场景 | 断言 |
|---|---|---|
| test_ensure_create | 会话不存在 | 依次执行 new-session / set-option -g / set-option -w，参数正确 |
| test_ensure_takeover | 会话已存在 | 不执行任何写命令 |
| test_ensure_create_fail | new-session 返回码 1 | 抛 TmuxError |
| test_pane_dead_parse | stdout `0` / `1` / 其他 / 命令失败 | False / True / True / True |
| test_respawn_command | mock | 命令为 `respawn-pane -k -t <s> <cmd...>` |
| test_recovery_first_death | 存活很久后死亡 | 不计失败；通知第 1 次（5s）；sleep(5)；respawn 成功返回 True |
| test_recovery_early_death | respawn 后 30s 又死 | 记失败 1；通知第 2 次（15s） |
| test_recovery_halt | 连续 3 次死亡均不足 60s | 第 4 次死亡时熔断：不再 respawn，告警一次，halted=True |
| test_recovery_respawn_fail | respawn 抛 TmuxError | 立即记失败；下次死亡按下一档退避 |
| test_recovery_reset_after_60s | respawn 后存活 61s 再死 | 计数清零，重新从第 1 档 5s 开始 |
| test_recovery_window_slide | 失败时间戳超出 window | 旧失败被剔除，不熔断 |
| test_recovery_alarm_once | 熔断后继续死 | 告警只发一次 |
| test_recovery_revive_externally | sleep 后 pane 已活 | 跳过 respawn，返回 False |
| test_recovery_notify_text | 检查通知文本 | 与模板渲染结果一致（`第 2/3 次`、`15s`） |
