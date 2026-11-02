# 01 · 架构

## 1. 进程与线程模型

网关为**单进程**，内部四类线程：

| # | 线程 | 归属模块 | 职责 |
|---|---|---|---|
| 1 | HTTP 接入线程（`HTTPServer` 单线程，按连接顺序处理请求） | onebot.py | 接收 NapCat 事件上报，校验、去重、取文本、入站排队 |
| 2 | 入站工作线程（1 个） | input.py | 串行消费入站队列：执行控制指令或注入 tmux |
| 3 | 监控线程（1 个） | __main__.py 编排，使用 capture.py / session.py / mirror.py | 存活检测、退避恢复、capture-pane 轮询、差分、稳定判定、批量释放 |
| 4 | 出站发送线程（1 个） | mirror.py | 先取控制 lane，再取输出 lane；限速调用 OneBot API |

主线程做启动编排、线程健康检查与信号等待（见 `18-mod-main.md`）。

监控线程是**输出侧唯一写者**：输出基线、候选块、批量缓冲、generation 快照只允许它触碰。入站线程通过队列/事件请求它执行 `#clear` 等动作，不直接修改这些状态。

## 2. 模块依赖

```
__main__ ──▶ config / state / onebot / router / input / capture / mirror / session
onebot   ──▶ （不依赖其他业务模块；事件经回调交给 __main__ 的接线代码）
input    ──▶ router（指令解析）, mirror（控制回执、回显登记、mirror 开关、epoch）, session（注入目标会话名仅作参数）
capture  ──▶ （独立；仅标准库）
mirror   ──▶ onebot.send_private_msg（发送）, state（持久化 mirror 经回调）
session  ──▶ （独立；恢复通知经回调交给控制 lane）
router   ──▶ （纯函数，无依赖）
config / state ──▶ （独立）
```

禁止反向依赖与循环依赖；跨模块协作一律通过 `__main__` 接线时注入的回调或队列完成。

## 3. 数据流

### 3.1 入站（QQ → kimico）

```
NapCat POST /cq
  → onebot：鉴权 / 路径 / 方法 / 长度 / JSON 校验
  → onebot：post_type=message ∧ message_type=private ∧ user_id=主QQ 过滤（不满足→200 静默丢弃）
  → onebot：message_id 去重（重复→200 静默丢弃）
  → onebot：校验 message 为 list，提取 text 段，构造 PrivateMessageEvent
  → InboundQueue.try_put（满→控制 lane 放入 BUSY_REPLY，丢弃事件）
  → input worker 串行取出：
      ├─ 无有效 text 段        → 控制 lane 放入 NOT_TEXT_REPLY
      ├─ text.strip() == ""    → 静默忽略
      ├─ text 以 "##" 开头     → 去掉第一个 "#" 后注入
      ├─ "#" 控制指令          → 执行副作用 → 控制 lane 放入回执
      ├─ 未知 "#" 指令         → 控制 lane 放入 USAGE_TEXT
      └─ 普通文本              → session.inject 注入 tmux；成功→登记回显；失败→控制 lane 放入 INJECT_FAIL_REPLY
```

注入动作严格串行（单 worker），保证不交错；注入顺序与消息到达顺序一致。HTTP 接入使用单线程 `HTTPServer`，请求按连接顺序处理，天然保证 `try_put` 的到达顺序。

### 3.2 出站（kimico → QQ）

```
监控线程每 poll_ms 一轮：
  → get_pane_state：
      ALIVE   → recovery.note_alive()，继续抓取
      DEAD/MISSING → recovery.note_dead_and_recover()；成功重建/重启则重置基线；跳过本轮
      UNKNOWN → 记 warning，跳过本轮，不做任何 respawn
  → capture.capture_pane（带 timeout）
  → 与上次快照做 KMP 差分 find_overlap（O(n)）
  → k==0：
      先尝试 recover_new_lines（difflib 插入块恢复）
      恢复失败则连续 k0 计数 +1；达到 k0_rebuild_threshold 才重建基线并丢弃该片段
  → 重叠之后的新增行交给 StabilityTracker
  → 稳定释放的行（last_output 时间戳随之更新）交给 mirror.MirrorPipeline：
      ├─ mirror=off 或 generation 不匹配 → 丢弃
      ├─ 按 ignore_patterns 过滤行（默认丢弃纯空白行）
      ├─ EchoSuppressor.suppress 行级抵消回显 → 只删除命中的行
      ├─ 加入 pending_batch（静默窗口 batch_quiet_ms；超过 batch_max_chars 立即 flush）
      └─ flush 时 chunk_lines 切段 → OutboundBus.put_bulk(text, generation)
出站发送线程：
  → OutboundBus.get：优先控制 lane，其次输出 lane
  → 输出段 generation 不等于当前 epoch → 直接丢弃
  → onebot.send_private_msg（超时 3s；失败等 2s 重试一次；再失败记日志丢弃）
  → 控制段之间间隔 control_min_interval_ms（默认 100ms）；输出段之间 send_interval_ms
```

控制指令回执、恢复通知、告警统一进入控制 lane，**不参与输出 lane 的溢出丢弃**。

### 3.3 恢复流

```
get_pane_state：
  ALIVE    pane_dead=0
  DEAD     pane_dead=1，会话存在
  MISSING  会话不存在（tmux server 或 session 不存在）
  UNKNOWN  命令失败/超时/输出无法解析

DEAD    → respawn-pane（确认 dead 后才允许 -k）
MISSING → ensure_session 重建会话（受退避/熔断约束）
UNKNOWN → 只记 warning，下一轮重试；绝不 respawn
```

`RecoveryManager` 的退避、失败结算、熔断见 `17-mod-session.md` 第 4 节。一次恢复尝试只结算一次失败；网关启动时首次观察即 dead 不计失败。

## 4. 共享状态与同步

| 状态 | 持有者 | 同步方式 | 读取方 | 写入方 |
|---|---|---|---|---|
| mirror 开关 | mirror.MirrorSwitch | 内部 `threading.Lock` | 监控线程、入站线程（#status） | 入站线程（#mirror） |
| 输出 generation | mirror.OutputEpoch | 内部 `threading.Lock` | 监控线程、发送线程、入站线程 | 入站线程（#clear/#mirror off） |
| 入站队列 | input.InboundQueue | `queue.Queue` 自带 | 入站线程、HTTP 线程（#status） | HTTP 请求线程 |
| 控制 lane | mirror.OutboundBus | 内部 Condition + deque | 发送线程、入站线程（#status） | 全部线程（经 put_control） |
| 输出 lane | mirror.OutboundBus | 内部 Condition + deque | 发送线程、入站线程（#status/#mirror off/#clear） | 监控线程（经 put_bulk） |
| 输出基线 / 候选块 / 批量缓冲 | 监控线程局部 | 无需锁（单线程访问） | — | 监控线程 |
| 最后输出时间 | __main__.RuntimeState | 内部 `threading.Lock` | 入站线程（#status） | 监控线程 |
| 网关启动时间 | __main__.RuntimeState | 启动后只读 | 入站线程（#status） | 主线程 |
| 基线重置请求 | `threading.Event` | 自带（仅一次性信号，不承载数据） | 监控线程 | 入站线程（#clear） |
| 回显待抵消队列 | mirror.EchoSuppressor | 内部 `threading.Lock` | 监控线程 | 入站线程 |
| 恢复计数 | session.RecoveryManager | 仅监控线程访问 | 监控线程 | 监控线程 |

规则：

- 输出基线、候选块、批量缓冲、generation 快照**只允许监控线程**触碰；`#clear` 通过 Event 请求监控线程在下一轮重置，不直接改。
- 任何线程都可通过 `put_control` 发控制消息；`put_bulk` 只允许监控线程调用。
- `#mirror off` / `#clear` 只清输出 lane；控制 lane 永不被清空。
- 发送线程在发送前和重试前检查 segment.generation；不匹配则丢弃。
- 所有 tmux 子进程调用必须带超时（默认 5s）；超时按对应模块的 `TmuxError` / `CaptureError` / `InjectError` 处理。
- 不使用锁之外的原语做跨线程数据共享；`threading.Event` 仅作一次性信号，不承载业务数据。

## 5. 日志与退出约定

- 日志统一输出到 stdout，由 systemd/journald 收集；格式含时间、级别、模块名、消息。
- 任何包含 access_token 的内容不得写入日志；记录配置摘要时打码。
- 默认不记录消息正文、注入文本、kimico 输出内容；发送失败只记录长度、hash 前缀或前 20 字符的脱敏摘要（DEBUG 且显式开启时才记录更多）。
- 关键计数（建议以 INFO 每分钟汇总一次）：`capture_k0_total`、`bulk_dropped_total`、`send_fail_total`、`recovery_attempt_total`、`control_backlog`。
- SIGTERM/SIGINT → 停止 HTTP、join 入站线程、持久化 mirror、等待线程退出；**不动** tmux 会话与 kimico 进程（它们独立于网关存活）。
- 主线程每秒检查四类线程的 `is_alive()`；任一线程意外退出时记 CRITICAL，置位 `stop_event`，退出码 5，由 systemd 重启网关。
