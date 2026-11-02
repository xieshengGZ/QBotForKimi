# 18 · 模块规约：__main__.py

## 1. 职责

启动与线程编排：加载配置、初始化日志、恢复状态、自检 tmux 会话、组装各模块、启动四类线程、处理信号与优雅停机。不包含业务算法。

## 2. 包结构与运行入口

```
kimico_gateway/
├── __init__.py        # 空文件或版本号
├── __main__.py        # 本模块
├── config.py
├── state.py
├── onebot.py
├── router.py
├── input.py
├── capture.py
├── mirror.py
└── session.py
```

- 运行：`python3 -m kimico_gateway [config_path]`；`config_path` 缺省 `/etc/kimico-gateway/config.toml`。
- 退出码：正常停机 0；配置错误 2；tmux 自检失败 3；端口占用等启动期致命错误 4。

## 3. 共享运行时状态

```python
class RuntimeState:
    """线程安全的运行时共享状态。"""
    def __init__(self) -> None: ...
    started_monotonic: float      # 启动时刻 time.monotonic()（构造后只读）
    def set_last_output(self, ts: float) -> None: ...
    def last_output(self) -> float | None: ...   # 初始 None
```

## 4. 启动序列

按序执行，任一步失败按第 2 节退出码退出（错误打印到 stderr）：

1. 解析 argv，取 config_path。
2. `load_config(config_path)`（ConfigError → 退出码 2）。
3. 初始化 logging：输出到 stdout；格式 `%(asctime)s %(levelname)s %(name)s %(message)s`；级别取 `config.logging.level`。
4. `StateStore(config.state.path)`；`mirror0 = store.load()`。
5. `ensure_session(...)`：失败（TmuxError）→ stderr 打印并退出码 3。
6. 组装组件：
   - `RuntimeState()`
   - `MirrorSwitch(mirror0, on_change=store.save)`（持久化回调内捕获异常记 ERROR）
   - `EchoSuppressor(window_seconds=10.0)`
   - `OutboundQueue(config.mirror.outbound_queue_max, OVERFLOW_NOTICE)`
   - `InboundQueue(50)`
   - `DedupTable(200)`
   - `MirrorPipeline(switch, echo, queue, compile_patterns(config.mirror.ignore_patterns), config.mirror.chunk_chars)`
   - `RecoveryManager(config.recovery, notify=queue.put, respawn=lambda: respawn_pane(config.tmux.session, config.tmux.kimico_command), is_alive=lambda: not is_pane_dead(config.tmux.session), sleep=stop_event.wait)`
   - `reset_baseline = threading.Event()`、`stop_event = threading.Event()`
   - `status_provider`（闭包，见第 6 节）
7. 启动线程（全部 `daemon=True`）：
   - HTTP 接入线程：`OnebotServer(..., on_event=event_intake)` → `serve_forever()`；`event_intake(event)`：`inbound.try_put(event)`，满 → `queue.put(BUSY_REPLY)`。
   - 入站工作线程：`run_input_worker(...)`（参数见 `14-mod-input.md`）。
   - 监控线程：`run_monitor_loop(...)`（见第 5 节）。
   - 出站发送线程：`run_sender_loop(queue, config.onebot.api_base, config.onebot.access_token, config.auth.main_qq, config.mirror.send_interval_ms / 1000, stop_event)`。
8. 启动失败（如端口占用 OSError）→ stderr + 退出码 4。
9. 主线程注册 SIGTERM/SIGINT 处理器（置 `stop_event`），随后阻塞等待信号。
10. 停机序列（见第 7 节）。

## 5. 监控线程（run_monitor_loop）

```python
def run_monitor_loop(cfg, pipeline, reset_baseline, runtime, recovery, stop_event):
    prev: list[str] | None = None
    tracker = StabilityTracker(cfg.tmux.stable_rounds)
    poll = cfg.tmux.poll_ms / 1000
    while not stop_event.is_set():
        # 1. #clear 请求：重置基线（不回放）
        if reset_baseline.is_set():
            reset_baseline.clear()
            prev = None; tracker.reset()
        # 2. 存活检测与恢复
        if is_pane_dead(cfg.tmux.session):
            if recovery.note_dead_and_recover():
                prev = None; tracker.reset()      # respawn 成功 → 基线重置
            stop_event.wait(poll)
            continue
        recovery.note_alive()
        # 3. 抓取与差分（CaptureError → WARNING，跳过本轮）
        try:
            cur = capture_pane(cfg.tmux.session, cfg.tmux.capture_lines)
        except CaptureError:
            stop_event.wait(poll); continue
        if prev is None:
            prev = cur; tracker.reset()
            stop_event.wait(poll); continue
        k = find_overlap(prev, cur)
        if k == 0:
            log warning（基线重建）
            prev = cur; tracker.reset()
            stop_event.wait(poll); continue
        released = tracker.feed(cur[k:])
        prev = cur
        if released:
            runtime.set_last_output(time.time())
            pipeline.handle_stable_lines(released)
        stop_event.wait(poll)
```

- "最后输出时间"在释放稳定行时更新，**与 mirror 开关无关**（off 时基线照常推进、时间照常更新）。
- 轮询节奏为"处理完一轮后等 poll 秒"，不做漂移补偿（允许轻微变慢）。

## 6. status_provider 闭包

每次调用现场采集：

```python
def status_provider() -> StatusSnapshot:
    alive_session = session_exists(cfg.tmux.session)
    alive_pane = alive_session and not is_pane_dead(cfg.tmux.session)  # 短路：会话不存在时不调用
    return StatusSnapshot(
        tmux_alive=alive_session,
        kimico_alive=alive_pane,
        mirror_on=switch.is_on(),
        inbound_len=inbound.qsize(), inbound_max=50,
        outbound_len=queue.qsize(), outbound_max=config.mirror.outbound_queue_max,
        last_output_ts=runtime.last_output(),
        started_monotonic=runtime.started_monotonic,
    )
```

会话不存在时 `kimico_alive` 必须为 False（不调用 is_pane_dead）。

## 7. 停机序列

1. 信号处理器置 `stop_event`（可重入，多次信号幂等）。
2. `OnebotServer.shutdown()`（停止接新请求）。
3. `store.save(switch.is_on())`（最终持久化 mirror）。
4. join 各线程，每线程至多等 5s；超时记 WARNING 后照常退出（daemon 线程随进程结束）。
5. **不触碰** tmux 会话与 kimico 进程。
6. 退出码 0。

## 8. 边界条件

- 启动时 tmux 会话已存在且 kimico 正在运行：接管，输出基线从当前屏幕起算，不回放历史。
- 启动时 pane 已是 dead：接管后监控线程第一轮即进入恢复流程。
- 启动时 mirror=true（state 恢复）：从启动起即推送新稳定输出。
- Ctrl-C（SIGINT）与 SIGTERM 行为一致。
- 收到信号时若正处于退避 sleep：`stop_event.wait` 立即返回，循环退出。

## 9. 错误处理

| 场景 | 处理 |
|---|---|
| ConfigError | stderr 打印错误全文，退出码 2 |
| ensure_session TmuxError | stderr，退出码 3 |
| OnebotServer 启动失败（端口占用等） | stderr，退出码 4 |
| 运行期线程内异常 | 各模块自身消化（见各模块规约）；__main__ 不做兜底重启 |

## 10. 日志要求

| 事件 | 级别 |
|---|---|
| 启动（版本、config 路径、关键配置摘要，token 打码） | INFO |
| 会话接管/创建结果 | INFO |
| 信号收到、停机各步骤 | INFO |
| 线程 join 超时 | WARNING |

## 11. 测试清单

（以假组件做集成级接线测试；tmux/HTTP 均 mock 或起本地假服务。）

| 测试 | 场景 | 断言 |
|---|---|---|
| test_startup_happy | 全假组件启动后停掉 | 四类线程全部启动；退出码路径正常 |
| test_startup_config_fail | 坏配置 | 退出码 2，stderr 含错误 |
| test_startup_tmux_fail | ensure_session 抛 TmuxError | 退出码 3 |
| test_status_provider | 会话不存在 | tmux_alive=False、kimico_alive=False，且不调用 is_pane_dead |
| test_monitor_reset | reset_baseline 置位 | 下一轮 prev 重置、tracker.reset 被调用、当轮不产出 |
| test_monitor_respawn_resets | note_dead_and_recover 返回 True | 输出基线被重置 |
| test_shutdown | 启动后 SIGTERM | 1s 内触发停机；state.save 被调用；tmux 无 kill 类调用 |
| test_event_intake_full | inbound 满时 on_event | 事件丢弃，BUSY_REPLY 进入发送队列 |
