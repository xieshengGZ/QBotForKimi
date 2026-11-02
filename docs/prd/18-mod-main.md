# 18 · 模块规约：__main__.py

## 1. 职责

启动与线程编排：加载配置、初始化日志、恢复状态、预检 kimico 命令、自检 tmux 会话、组装各模块、启动四类线程、健康检查、处理信号与优雅停机。不包含业务算法。

## 2. 包结构与运行入口

```
kimico_gateway/
├── __init__.py        # 版本号
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

- 运行：`python3 -m kimico_gateway [config_path]`；`config_path` 缺省 `/var/lib/kimico/config.toml`。
- 退出码：正常停机 0；配置错误 2；kimico 命令预检/tmux 自检失败 3；端口占用等启动期致命错误 4；运行期线程死亡 5。

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
3. 初始化 logging：输出到 stdout；格式 `%(asctime)s %(levelname)s %(name)s %(message)s`；级别取 `config.logging.level`；`logging.raiseExceptions = False`。
4. `StateStore(config.state.path)`；`mirror0 = store.load()`。
5. 预检 `config.tmux.kimico_command`：`argv = shlex.split(...)`；空或 `shutil.which(argv[0]) is None` → stderr 明确提示并退出码 3。预检不执行 kimico，只确认可找到。
6. `ensure_session(config.tmux.session, config.tmux.kimico_command, config.tmux.history_limit)`：失败（TmuxError）→ stderr 打印并退出码 3。
7. 组装组件：
   - `RuntimeState()`
   - `MirrorSwitch(mirror0, on_change=store.save)`（回调内捕获异常记 ERROR）
   - `OutputEpoch()`
   - `EchoSuppressor(window_seconds=10.0, max_entries=50, prefix_patterns=compile_patterns(config.mirror.echo_prefix_patterns))`
   - `OutboundBus(config.mirror.outbound_queue_max, OVERFLOW_NOTICE, control_soft_max=100)`
   - `InboundQueue(50)`
   - `DedupTable(200)`
   - `MirrorPipeline(switch, epoch, echo, bus, compile_patterns(config.mirror.ignore_patterns), config.mirror.chunk_chars, config.mirror.batch_quiet_ms, config.mirror.batch_max_chars)`
   - `RecoveryManager(config.recovery, notify=bus.put_control, respawn=lambda: respawn_pane(...), recreate=lambda: ensure_session(...), pane_state=lambda: get_pane_state(config.tmux.session), sleep=stop_event.wait)`
   - `reset_baseline = threading.Event()`、`stop_event = threading.Event()`
   - `status_provider`（闭包，见第 6 节）
8. 构造 `OnebotServer(...)`（绑定端口在此发生）；`OSError`（端口占用等）→ stderr + 退出码 4。
9. 启动线程（全部 `daemon=True`，但主线程会 join）：
   - HTTP 接入线程：`OnebotServer.serve_forever()`。
   - 入站工作线程：`run_input_worker(...)`。
   - 监控线程：`run_monitor_loop(...)`。
   - 出站发送线程：`run_sender_loop(bus, epoch, api_base, access_token, main_qq, send_interval, 0.1, stop_event)`。
   - `event_intake(event)`：`inbound.try_put(event)`；满 → `bus.put_control(BUSY_REPLY)`。
10. 主线程注册 SIGTERM/SIGINT 处理器（置 `stop_event`），进入健康检查循环（第 8 节）。
11. 停机序列（见第 9 节）。

## 5. 监控线程（run_monitor_loop）

```python
def run_monitor_loop(cfg, pipeline, reset_baseline, runtime, recovery, epoch, stop_event):
    prev: list[str] | None = None
    tracker = StabilityTracker(cfg.tmux.stable_rounds)
    k0_streak = 0
    poll = cfg.tmux.poll_ms / 1000
    while not stop_event.is_set():
        try:
            # 1. #clear 请求：清批处理缓冲并重置基线（不回放）
            if reset_baseline.is_set():
                reset_baseline.clear()
                pipeline.clear()
                prev = None; tracker.reset(); k0_streak = 0

            # 2. 四态存活检测与恢复
            state = get_pane_state(cfg.tmux.session)
            if state == PaneState.ALIVE:
                recovery.note_alive()
            elif state in (PaneState.DEAD, PaneState.MISSING):
                if recovery.note_dead_and_recover(state):
                    prev = None; tracker.reset(); pipeline.clear(); k0_streak = 0
                pipeline.flush_if_due()
                stop_event.wait(poll)
                continue
            else:  # UNKNOWN
                log warning（不 respawn）
                pipeline.flush_if_due()
                stop_event.wait(poll)
                continue

            # 3. 抓取与差分
            cur = capture_pane(cfg.tmux.session, cfg.tmux.capture_lines)
            if prev is None:
                prev = cur; tracker.reset(); k0_streak = 0
                pipeline.flush_if_due()
                stop_event.wait(poll); continue

            pm = mask_tail(prev, cfg.tmux.volatile_tail_lines)
            cm = mask_tail(cur, cfg.tmux.volatile_tail_lines)
            k = find_overlap(pm, cm)
            if k == 0:
                recovered = recover_new_lines(prev, cur)
                if recovered:
                    new_lines = recovered; k0_streak = 0
                else:
                    k0_streak += 1
                    if k0_streak >= cfg.tmux.k0_rebuild_threshold:
                        log warning（基线重建）
                        prev = cur; tracker.reset(); k0_streak = 0
                    pipeline.flush_if_due()
                    stop_event.wait(poll); continue
            else:
                k0_streak = 0
                new_lines = cur[k:]

            prev = cur
            released = tracker.feed(new_lines)
            if released:
                runtime.set_last_output(time.time())
                pipeline.handle_stable_lines(released, epoch.current())
            pipeline.flush_if_due()
        except Exception:
            log exception（含 traceback）
        stop_event.wait(poll)
```

- “最后输出时间”在释放稳定行时更新，**与 mirror 开关无关**（off 时基线照常推进、时间照常更新）。
- 轮询节奏为“处理完一轮后等 poll 秒”，不做漂移补偿（允许轻微变慢）。
- 监控线程外层每轮 catch-all；任何单轮异常不得让线程退出。

## 6. status_provider 闭包

每次调用现场采集：

```python
def status_provider() -> StatusSnapshot:
    state = get_pane_state(cfg.tmux.session)   # 不抛异常；失败返回 UNKNOWN
    tmux_alive = state in (PaneState.ALIVE, PaneState.DEAD)
    kimico_alive = state == PaneState.ALIVE
    return StatusSnapshot(
        tmux_alive=tmux_alive,
        kimico_alive=kimico_alive,
        mirror_on=switch.is_on(),
        inbound_len=inbound.qsize(), inbound_max=50,
        control_len=bus.qsize_control(), control_max=100,
        bulk_len=bus.qsize_bulk(), bulk_max=config.mirror.outbound_queue_max,
        last_output_ts=runtime.last_output(),
        started_monotonic=runtime.started_monotonic,
    )
```

`get_pane_state` 应带 5s 超时；`#status` 最坏阻塞 5s，可接受。

## 7. 出站发送线程

```python
run_sender_loop(
    bus, epoch,
    config.onebot.api_base,
    config.onebot.access_token,
    config.auth.main_qq,
    config.mirror.send_interval_ms / 1000,
    0.1,                       # control_interval
    stop_event,
)
```

发送线程每轮 catch-all；单段异常不得让线程退出（见 `16-mod-mirror.md`）。

## 8. 健康检查与停机序列

主线程循环：

```python
failed = False
while not stop_event.wait(1.0):
    for name, t in threads.items():
        if not t.is_alive():
            log.critical("thread died: %s", name)
            failed = True
            stop_event.set()
            break
```

停机序列：

1. `stop_event.set()`（信号处理器可重入，多次信号幂等）。
2. `OnebotServer.shutdown()`（停止接新请求）。
3. join 入站工作线程，至多 5s；超时记 WARNING。
4. `store.save(switch.is_on())`（最终持久化 mirror；入站线程已停，避免竞争）。
5. join 监控/发送/HTTP 线程，每线程至多 5s；超时记 WARNING 后照常退出（daemon 线程随进程结束）。
6. **不触碰** tmux 会话与 kimico 进程。
7. 退出码：正常 0；若第 8 节检测到线程死亡，退出码 5。

## 9. 边界条件

- 启动时 tmux 会话已存在且 kimico 正在运行：接管；接管路径幂等设置 `remain-on-exit on`；输出基线从当前屏幕起算，不回放历史。
- 启动时 pane 已是 dead：接管后监控线程第一轮检测 DEAD，首次不计失败，等待 5s 后 respawn。
- 启动时 mirror=true（state 恢复）：从启动起即推送新稳定输出。
- Ctrl-C（SIGINT）与 SIGTERM 行为一致。
- 收到信号时若正处于退避 sleep：`stop_event.wait` 立即返回，循环退出。
- 线程死亡：主线程检测到后置位 `stop_event`，走停机序列并以 5 退出，由 systemd 重启；不得静默继续。

## 10. 错误处理

| 场景 | 处理 |
|---|---|
| ConfigError | stderr 打印错误全文，退出码 2 |
| kimico_command 预检失败 | stderr 明确提示，退出码 3 |
| ensure_session TmuxError | stderr，退出码 3 |
| OnebotServer 构造/绑定失败（端口占用等） | stderr，退出码 4 |
| 运行期单轮异常 | 模块内 catch-all，记 ERROR，继续 |
| 运行期线程死亡 | 主线程置 stop，退出码 5，由 systemd 重启 |

## 11. 日志要求

| 事件 | 级别 |
|---|---|
| 启动（版本、config 路径、关键配置摘要，token 打码） | INFO |
| 会话接管/创建结果（history-limit/remain-on-exit 校验） | INFO |
| 信号收到、停机各步骤 | INFO |
| 线程死亡 | CRITICAL |
| 线程 join 超时 | WARNING |
| 每分钟计数汇总（k0/丢弃/发送失败/恢复次数） | INFO |

## 12. 测试清单

（以假组件做集成级接线测试；tmux/HTTP 均 mock 或起本地假服务。）

| 测试 | 场景 | 断言 |
|---|---|---|
| test_startup_happy | 全假组件启动后停掉 | 四类线程全部启动；退出码 0 |
| test_startup_config_fail | 坏配置 | 退出码 2，stderr 含错误 |
| test_startup_kimico_missing | `kimico_command` 找不到 | 退出码 3，stderr 含命令名 |
| test_startup_tmux_fail | ensure_session 抛 TmuxError | 退出码 3 |
| test_startup_port_busy | 端口占用 | 退出码 4 |
| test_status_provider | 会话不存在 | tmux_alive=False、kimico_alive=False；控制/输出队列字段正确 |
| test_status_provider_unknown | get_pane_state UNKNOWN | tmux_alive=False、kimico_alive=False，不抛异常 |
| test_monitor_reset | reset_baseline 置位 | 下一轮 prev 重置、pipeline.clear/tracker.reset 被调用、当轮不产出 |
| test_monitor_respawn_resets | note_dead_and_recover 返回 True | 输出基线被重置，pipeline 清空 |
| test_monitor_unknown_no_recovery | state=UNKNOWN | 不调用 note_dead_and_recover |
| test_monitor_k0_recover | k=0 但 recover 有插入 | 不重建基线，released 进入 pipeline |
| test_monitor_k0_threshold | 连续 k=0 达阈值 | 重建基线一次 |
| test_thread_death | 人为让发送线程退出 | 主线程 1s 内检测，stop_event 置位，退出码 5 |
| test_shutdown | 启动后 SIGTERM | 1s 内触发停机；入站 join 后 state.save 被调用；tmux 无 kill 类调用 |
| test_event_intake_full | inbound 满时 on_event | 事件丢弃，BUSY_REPLY 进入控制 lane |
