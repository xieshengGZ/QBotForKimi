# 01 · 架构

## 1. 进程与线程模型

网关为**单进程**，内部四类线程：

| # | 线程 | 归属模块 | 职责 |
|---|---|---|---|
| 1 | HTTP 接入线程（`ThreadingHTTPServer` 主循环；每个请求由内部 worker 线程处理） | onebot.py | 接收 NapCat 事件上报，校验、去重、取文本、入站排队 |
| 2 | 入站工作线程（1 个） | input.py | 串行消费入站队列：执行控制指令或注入 tmux |
| 3 | 监控线程（1 个） | __main__.py 编排，使用 capture.py / session.py | 存活检测、退避恢复、capture-pane 轮询、差分、稳定判定 |
| 4 | 出站发送线程（1 个） | mirror.py | 从发送队列取段，限速调用 OneBot API |

主线程仅做启动编排与信号等待（见 `18-mod-main.md`）。

## 2. 模块依赖

```
__main__ ──▶ config / state / onebot / router / input / capture / mirror / session
onebot   ──▶ （不依赖其他业务模块；事件经回调交给 __main__ 的接线代码）
input    ──▶ router（指令解析）, mirror（回执入发送队列、回显登记、mirror 开关）, session（注入目标会话名仅作参数）
capture  ──▶ （独立）
mirror   ──▶ onebot.send_private_msg（发送）, state（持久化 mirror 经回调）
session  ──▶ （独立；恢复通知经回调交给发送队列）
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
  → onebot：提取文本段，构造 PrivateMessageEvent
  → InboundQueue.try_put（满→发送"当前繁忙，请稍后重试"，丢弃事件）
  → input worker 串行取出：
      ├─ 无文本段        → 发送"仅支持文本消息"
      ├─ 文本为空串      → 静默忽略
      ├─ # 控制指令      → 执行副作用（mirror/状态/清理）→ 发送回执
      ├─ 未知 # 指令     → 发送用法提示
      └─ 普通文本        → session.inject 注入 tmux；成功→登记回显；失败→发送"注入失败，请稍后重试"并记日志
```

注入动作严格串行（单 worker），保证不交错；注入顺序与消息到达顺序一致。

### 3.2 出站（kimico → QQ）

```
监控线程每 poll_ms 一轮：
  → pane dead？→ 交给 session.RecoveryManager（恢复期间跳过输出处理）
  → capture.capture_pane 抓取行列表
  → 与上次快照做尾部/头部重叠匹配（k=0→重建基线、丢弃片段、记 warning）
  → 重叠之后的新增行交给 StabilityTracker
  → 稳定释放的行（last_output 时间戳随之更新）交给 mirror.MirrorPipeline：
      ├─ mirror=off → 丢弃（基线已推进，重开不回放）
      ├─ 按 ignore_patterns 过滤行（默认丢弃纯空白行）
      ├─ EchoSuppressor 匹配回显 → 命中则整块跳过（只抵消一次）
      └─ chunk_lines 切段 → OutboundQueue
出站发送线程：
  → 逐段取出 → onebot.send_private_msg（超时 3s；失败等 2s 重试一次；再失败记日志丢弃）
  → 每段处理后间隔 send_interval_ms
```

控制指令回执、恢复通知、告警也统一进入 OutboundQueue，走同一条发送路径。

### 3.3 恢复流

pane dead → RecoveryManager：按 backoff_seconds 退避 → respawn-pane → 判定成败（退出码 0 且存活满 success_reset_seconds）→ 失败计入滑动窗口 → 窗口内累计 ≥ max_attempts 次失败则停止恢复并告警一次。详见 `17-mod-session.md`。

## 4. 共享状态与同步

| 状态 | 持有者 | 同步方式 | 读取方 | 写入方 |
|---|---|---|---|---|
| mirror 开关 | mirror.MirrorSwitch | 内部 `threading.Lock` | 监控线程（管道）、入站线程（#status） | 入站线程（#mirror） |
| 入站队列 | input.InboundQueue | `queue.Queue` 自带 | 入站线程、（#status 经 size） | HTTP 请求线程 |
| 发送队列 | mirror.OutboundQueue | `queue.Queue` 派生，内部锁 | 发送线程、入站线程（#status/#mirror off/#clear） | 全部线程（经 put） |
| 输出基线 / 候选块 | 监控线程局部 | 无需锁（单线程访问） | — | 监控线程 |
| 最后输出时间 | __main__.RuntimeState | 内部 `threading.Lock` | 入站线程（#status） | 监控线程 |
| 网关启动时间 | __main__.RuntimeState | 启动后只读 | 入站线程（#status） | 主线程 |
| 基线重置请求 | `threading.Event` | 自带 | 监控线程 | 入站线程（#clear） |
| 回显待抵消队列 | mirror.EchoSuppressor | 内部 `threading.Lock` | 监控线程 | 入站线程 |
| 恢复计数 | session.RecoveryManager | 仅监控线程访问 | 监控线程 | 监控线程 |

规则：

- 输出基线与候选块**只允许监控线程**触碰；`#clear` 通过 Event 请求监控线程在下一轮重置，不直接改。
- 发送队列是唯一汇合点，任何线程都可通过 `put` 发送 QQ 消息。
- 不使用锁之外的原语做跨线程状态共享；共享对象的方法自身保证线程安全。

## 5. 日志与退出约定

- 日志统一输出到 stdout，由 systemd/journald 收集；格式含时间、级别、模块名、消息。
- 任何包含 access_token 的内容不得写入日志；记录配置摘要时打码。
- SIGTERM/SIGINT → 停止 HTTP、持久化 mirror、等待线程退出；**不动** tmux 会话与 kimico 进程（它们独立于网关存活）。
