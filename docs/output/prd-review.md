# kimico QQ 远程控制网关 · PRD 评审报告

> 状态：本报告针对 v1.0；对应修复已落到 `docs/prd/` v1.1（见 `docs/prd/CHANGELOG.md`）。报告保留作为变更追溯。

- 评审对象：`docs/prd/` 全部 17 份文档（约 140 KB）
- 评审方式：全文通读 + 交叉比对 + 3 项本地事实核验（tmux 3.4 / systemd / Python 3.12）
- 评审配置：按“尽可能简单”执行——单一报告、桌面评审为主、不单独出 issue register / spike 目录、P0/P1 优先
- 结论：**当前版本不建议直接作为 AI 实现的唯一依据。** 存在 9 个 P0 阻断项；其中“网关重启不影响 kimico”“控制回执始终发送”“#clear 后不回放”三个核心承诺在现有规约下并不成立。

---

## 0. 一页纸结论

| 维度 | 结论 |
|---|---|
| 可直接实现性 | 模块边界清楚，但关键路径存在多处自相矛盾，AI 实现者会在歧义处自行发挥 |
| 核心承诺 | 部署、并发、队列三处设计会让承诺失效，不是文案问题 |
| 最大风险 | 真实 kimico 是 TUI；轮询 + 行差分 + 回显过滤没有经过真机验证，可能在真实提示符/状态栏/重绘下完全失效 |
| 建议 | 先修 9 个 P0，补 6 类验收，再冻结 v1.1；不要在当前版本上直接进入编码 |

**最严重的三件事：**

1. `systemctl restart` 会连带杀掉网关拉起的 tmux server 和 kimico，直接违反产品目标。
2. `#clear`、`#mirror off` 与监控线程存在竞态；控制回执、重启通知、熔断告警与大量输出共用同一个有损队列，可能被丢弃或延迟数百秒。
3. 轮询差分遇到 TUI 底部状态栏/进度条时，`k=0` 会不断重建基线并丢弃输出；回显过滤又以“整块完全相等”为条件，多行回答时可能重复或吞掉内容。

---

## 1. 优先级定义

| 级别 | 定义 | 处理要求 |
|---|---|---|
| P0 | 阻断交付，或使核心承诺/验收不成立 | 进入实现前必须修复，并补对应验收 |
| P1 | 会导致返工、验收不稳定、运维事故或明显体验问题 | v1.1 冻结前修复，或明确接受并记录 |
| P2 | 可维护性、文档一致性、边缘体验 | 可排期，不阻断 |

---

## 2. P0 阻断项

### P0-01 部署：systemd 默认会连带杀掉 tmux/kimico

**证据**

- `docs/prd/30-deploy.md:46-56` 的 unit 没有设置 `KillMode`。
- `man systemd.kill`：`KillMode` 默认 `control-group`，“all remaining processes in the control group of this unit will be killed on unit stop”。
- `docs/prd/00-overview.md:26` 承诺“网关进程崩溃不影响 tmux server 与 kimico 存活”；`docs/prd/20-acceptance.md:136-140` 的验收 9 直接使用 `systemctl restart`。

**问题**

只要 tmux server 是网关首次拉起的（`ensure_session` 的创建路径），它就继承了网关 service 的 cgroup。`systemctl restart` 会先 stop unit，杀掉 cgroup 内所有进程，tmux server 和 kimico 一起消失。验收 9 会失败，且 kimico 内存上下文丢失。

**最佳实践方案**

短期修复（最小改动）：

```ini
[Service]
KillMode=process
```

`KillMode=process` 只杀主进程，tmux server 留在 cgroup 中继续存活。它被 systemd 官方标注为“不推荐”，所以只作为过渡。

推荐结构（长期）：

- 把 tmux server 的生命周期从网关 unit 中拆出去：由用户的 login session 或独立的 `kimico-tmux.service` 先启动 tmux server；网关只做 `ensure_session` 接管，不负责首次创建。
- 网关 unit 保持 `KillMode=control-group`，但 cgroup 内不再包含 tmux server。
- 增加验收：`systemctl restart kimico-gateway` 后，`tmux display-message -p -t kimico '#{pane_dead}'` 仍为 `0`，且 kimico PID 不变。

**验证**

```bash
before=$(tmux display-message -p -t kimico '#{pid}')
sudo systemctl restart kimico-gateway
after=$(tmux display-message -p -t kimico '#{pid}')
[ "$before" = "$after" ] && echo PASS
```

---

### P0-02 部署：config.toml 的 600 权限属于 root，服务用户读不到

**证据**

- `docs/prd/30-deploy.md:27-31`：`sudo cp config.toml /etc/kimico-gateway/config.toml`，随后 `sudo chmod 600`；unit 使用 `User=ubuntu`。
- 文档自己写的是“600，属运行用户”，但安装命令没有 `chown`。

**问题**

`cp` 后文件属主是 root，`chmod 600` 后只有 root 可读；`User=ubuntu` 启动时 `load_config` 失败，退出码 2，systemd 反复重启。

**最佳实践方案**

```bash
sudo install -o ubuntu -g ubuntu -m 600 \
  config.toml /etc/kimico-gateway/config.toml
```

并在部署手册增加自检：

```bash
sudo -u ubuntu test -r /etc/kimico-gateway/config.toml && echo PASS
```

---

### P0-03 会话：history-limit 在 pane 创建之后才设置，实际只有约 2000 行

**证据**

- `docs/prd/17-mod-session.md:57-63`：先 `new-session`，再 `set-option -g history-limit`。
- `man tmux`：`history-limit` “applies only to new windows - existing window histories are not resized and retain the limit at the point they were created.”
- 本地 tmux 3.4 实测：先建会话、后设 50000，旧 pane 只能抓到约 2001 行；后建 pane 可抓到约 4001 行。全局值显示 50000，但旧 pane 不生效。

**问题**

`capture_lines = 5000`、`history_limit = 50000` 的组合在创建路径上失效。长输出、`#clear` 前的滚动内容、验收 6 的 5000 行输出都会被截断；AI 实现者按文档写，测试却对不上。

**最佳实践方案**

先设全局选项，再创建窗口/pane：

```bash
tmux set-option -g history-limit 50000
tmux new-session -d -s kimico -- <kimico argv>
tmux set-option -w -t kimico remain-on-exit on
```

接管路径无法扩容已有 pane 的历史；至少要做到：

```bash
tmux show-options -gv history-limit   # 低于期望值则 warning
```

如果低于期望值，明确记录“长输出可能被截断”，或在用户确认后重建会话。不要假装已经生效。

**验证**

让 kimico 输出 3000 行后执行：

```bash
tmux capture-pane -p -J -S -5000 -t kimico | wc -l
```

应显著大于 2000，而不是 2001。

---

### P0-04 会话：接管路径不设置 remain-on-exit，已有会话会让恢复流程失效

**证据**

- `docs/prd/17-mod-session.md:64`：“`ensure_session` 接管路径（会话已存在）：不改写任何配置，直接返回。”
- `docs/prd/17-mod-session.md:60`、`17-mod-session.md:104`：依赖 `remain-on-exit` 保留 dead pane，才能 `respawn-pane`。

**问题**

如果用户先手动创建了 `kimico` 会话（很常见：先跑起来再装网关），该会话没有 `remain-on-exit on`。kimico 退出后 window 直接关闭，`is_pane_dead` 返回 True，但 `respawn-pane` 找不到 pane，连续失败后熔断。验收 8 在“已有会话”场景下不成立。

**最佳实践方案**

接管路径做幂等配置，而不是完全不动：

```bash
tmux set-option -w -t <session> remain-on-exit on
tmux show-options -wv -t <session> remain-on-exit   # 校验为 on
```

同时校验会话结构：恰好一个 window、一个 pane；不符合则启动失败并给出明确错误，不要带着不确定结构进入恢复流程。

---

### P0-05 恢复：命令失败被当成 dead，`respawn-pane -k` 可能杀掉仍然活着的 kimico

**证据**

- `docs/prd/17-mod-session.md:65`：“命令返回码非零（含会话不存在）→ 返回 True（dead）。”
- `docs/prd/17-mod-session.md:61`：恢复使用 `tmux respawn-pane -k`，`-k` 会杀掉 pane 内现有进程。
- `docs/prd/17-mod-session.md:80-86`：失败计数在 respawn 失败时记一次，下一轮又因 `alive_since is None` 再记一次；启动时已是 dead 也会先记一次失败。

**问题**

三类事故：

1. **误杀**：tmux 短暂不可用、socket 抖动、命令超时，`is_pane_dead` 返回 True；恢复流程用 `-k` 把仍然活着的 kimico 杀掉，内存上下文丢失。
2. **重复计数**：respawn 抛 `TmuxError` 时记一次失败；下一轮 `alive_since` 为 None，第 2 步又记一次，一次失败算两次，提前熔断。
3. **首死计错**：网关启动时 pane 已是 dead，`alive_since` 为 None，第一次恢复就按“第 2 次”退避 15s，而不是 5s。

**最佳实践方案**

把 `is_pane_dead` 改成三态，恢复只对 `DEAD` 动作：

```python
class PaneState(Enum):
    ALIVE = auto()
    DEAD = auto()
    UNKNOWN = auto()   # tmux 命令失败、输出非 0/1、超时
```

- `ALIVE`：`pane_dead=0`。
- `DEAD`：`pane_dead=1`，且 session 明确存在。
- `UNKNOWN`：任何命令失败/超时/无法解析；只记 warning，下一轮重试，**绝不 respawn**。
- 只有 `DEAD` 才允许 `respawn-pane`；确认 dead 后 `-k` 是安全的。

恢复计数改成“一次尝试只结算一次”：

```text
状态：alive_since, pending_attempt, failures

每轮 dead：
  if pending_attempt:                 # 上次 respawn 的结果结算
      if alive_since and now - alive_since >= success_reset:
          failures.clear()
      else:
          failures.append(now)        # 只记这一次
      pending_attempt = False
      alive_since = None
  else:
      if alive_since is not None:
          if now - alive_since >= success_reset:
              failures.clear()
          else:
              failures.append(now)    # 活着但没活够，算一次失败
      # alive_since is None：首次观察即 dead，不计失败

  failures = [t for t in failures if now - t < window]
  if len(failures) >= max_attempts: halt + alarm once
  attempt = len(failures) + 1
  wait = backoff[min(len(failures), len(backoff)-1)]
  notify(...); sleep(wait)
  if is_alive(): alive_since = now; pending_attempt = False; return False
  try:
      respawn()
      alive_since = now
      pending_attempt = True
      return True
  except TmuxError:
      failures.append(now)            # 本次 respawn 失败，只记一次
      pending_attempt = False
      return False
```

补测试：启动即 dead 第一次等 5s；respawn 失败只计 1 次；连续 3 次失败后第 4 次死亡才熔断；窗口滑动；外部复活。

---

### P0-06 并发：`#clear` / `#mirror off` 与监控线程存在竞态

**证据**

- `docs/prd/14-mod-input.md:67-70`：`#clear` 执行 `outbound.clear()` → `reset_baseline.set()` → 回执；`#mirror off` 执行 `set(False)` → `clear()` → 回执。
- `docs/prd/18-mod-main.md:74-102`：监控线程只在每轮开头检查 `reset_baseline`；它可能已经执行完 `tracker.feed()`，正处于 `pipeline.handle_stable_lines()`。
- `docs/prd/15-mod-capture.md:142`：`#clear` 在下一轮开头才重置基线。

**问题**

- `#clear` 清空队列后，监控线程仍可能把旧行重新入队；用户看到“已清空”之后又收到旧输出。验收 5 会不稳定。
- `#mirror off` 同理：监控线程已经读到 `is_on()==True`，在 `clear()` 之后入队；关闭后仍有一条旧输出。
- 发送线程已经取出的段无法撤回；`#mirror off` 后仍可能收到在途段。文档承认在途段，但没有区分“在途”与“清空后新入队”。

**最佳实践方案**

方案 A（推荐，结构最简单）：**单写者命令队列**。

- 入站 worker 不直接改基线/队列，而是把 `ClearCommand`、`MirrorOffCommand` 放入监控线程的命令队列。
- 监控线程在每轮安全点处理命令：先 `pipeline.clear()`，再 `prev = capture_pane()`、`tracker.reset()`，然后回 ACK。
- 入站 worker 收到 ACK 后再发“已清空/已关闭”回执。这样回执不会早于实际状态。
- 发送线程也消费同一个 generation；在发送前和重试前检查 generation，旧段直接丢弃。

方案 B（改动较小）：**generation + 同一把锁**。

```python
with pipeline_lock:
    generation += 1
    outbound.clear()

# 监控线程
with pipeline_lock:
    if gen_at_round != generation:
        return
    outbound.put(...)
```

`#clear` 的基线重置仍需监控线程执行；回执应等监控线程确认，或把文案改成“已请求清空”。

**验证**

并发测试：监控线程持续产出，循环 100 次 `#clear`，断言每次回执后 1s 内队列为空且没有旧 generation 的段。`#mirror off` 同理。

---

### P0-07 队列：控制回执、重启通知、熔断告警与大量输出共用同一个有损队列

**证据**

- `docs/prd/00-overview.md:68`：“控制指令回执始终发送，不受 mirror 影响。”
- `docs/prd/16-mod-mirror.md:39-47`：`OutboundQueue` 容量满时丢弃最旧。
- `docs/prd/16-mod-mirror.md:69-71`：`#mirror off` 和 `#clear` 都调用 `OutboundQueue.clear()`。
- `docs/prd/18-mod-main.md:58`：恢复通知、告警也进入同一个 `OutboundQueue`。

**问题**

- 输出洪峰时，`#status` 回执可能排在 500 段之后，按 1s 间隔要等 500 秒；或者直接被溢出逻辑丢掉。
- `#mirror off` / `#clear` 会清掉尚未发送的重启通知和熔断告警；用户最需要告警时收不到。
- “回执始终发送”在实现上不成立；这是产品承诺与队列结构的直接冲突。

**最佳实践方案**

拆成两条 lane，发送线程优先消费控制 lane：

```python
control_q = queue.Queue(maxsize=100)   # 回执/通知/告警，不因 bulk 溢出而丢
bulk_q = queue.Queue(maxsize=500)      # kimico 输出，可丢最旧
```

- `control_q` 满时：记 ERROR，丢弃最旧的控制消息也要发一条“控制通道过载”告警；不要静默丢。
- `#mirror off` / `#clear` 只清 `bulk_q`，不清 `control_q`。
- 发送线程：每轮先 `control_q.get_nowait()`，没有再取 `bulk_q`。
- 控制消息不参与 `send_interval` 的 1s 限速，或只受一个更小的最小间隔约束；QQ 风控由全局令牌桶控制。
- 所有控制/告警消息带 generation；`#clear` 后旧 generation 的 bulk 段在发送前丢弃。

**验证**

bulk 灌满 500 段后发 `#status`，断言 2s 内收到回执；再触发一次告警，断言告警不被 `#mirror off` 清掉。

---

### P0-08 回显：整块完全相等才抵消，多行回答会重复或吞内容

**证据**

- `docs/prd/16-mod-mirror.md:21-29`：`EchoSuppressor.match(lines)` 把整块 `normalize("\n".join(lines))` 与队首注入文本比较，相等才整块跳过。
- `docs/prd/16-mod-mirror.md:131-137`：过滤后的整块与队首比较；不相等则整块放行。
- `docs/prd/20-acceptance.md:65-72`：验收 2 要求“注入文本本身的回显被抵消，不应单独出现一条”。

**问题**

- **假阴性**：只要稳定块里同时包含回显和回答（多行回答、提示符前缀 `> `、`│ `、状态行），整块就不等于注入文本，回显会单独推送到手机。验收 2 在真实 TUI 下不稳定。
- **假阳性**：如果整块恰好等于注入文本，整块被跳过；若块里其实包含回答，回答被吞掉。
- `normalize` 只处理空白，不处理提示符、ANSI 残留、TUI 装饰符；真实 kimico 的提示符一变，过滤就失效。
- `register` 在 `inject` 返回后才调用；如果 capture 恰好在中间发生，回显可能先被释放，之后才登记，导致重复。
- mirror off 时 `handle_stable_lines` 直接丢弃，但 `echo.register` 仍会入队；`match` 不被调用，过期项不会被清理，长时间 off 会积累内存。

**最佳实践方案**

优先做“行级、保守”的抵消，而不是整块：

1. 过滤后，只检查块的前 1–N 行；若某行 `normalize(line)` 等于注入文本，或匹配 `prompt_prefix + 注入文本`，只删除该行，保留块内其余行。
2. 提示符前缀做成可配置正则，例如 `^\s*[>›$#│]\s*`；默认只匹配明确的提示符，不匹配正文。
3. 无法确定时**宁可重复，不可吞内容**：重复只是体验问题，吞内容是数据丢失。
4. `register` 前先清理过期项；给队列设上限（如 50），超出丢最旧。
5. 如果真实 kimico 的提示符复杂，产品上更简单的选择是**不抵消回显**：把用户自己发的消息也当作聊天记录显示，另加一条 `已发送` ACK。这比维护脆弱的正则更可靠。

**验证**

构造三类块：`["测试消息A"]`、`["测试消息A", "回答第一行"]`、`["> 测试消息A", "回答"]`；断言第一类被抵消，第二类保留回答，第三类至少保留回答且不重复整块。

---

### P0-09 捕获：TUI 底部状态行会让 `find_overlap` 反复 `k=0`，输出被丢弃

**证据**

- `docs/prd/15-mod-capture.md:43-77`：差分依赖 `prev[-k:] == cur[:k]`；`k=0` 时重建基线并丢弃该片段。
- `docs/prd/15-mod-capture.md:144`：文档自己承认“屏幕清空、末行原地改写”是预期内事件。
- `docs/prd/00-overview.md` 的已知限制只提“约 1 秒延迟”，没有把 TUI 重绘列为风险。

**问题**

kimico 是交互式 TUI，底部通常有状态栏、spinner、进度条或输入提示。它们每帧都可能变化，导致 `prev[-1] != cur[-1]`，`find_overlap` 返回 0；监控线程每轮都“重建基线并丢弃片段”。结果可能是：**永远推不出输出，或者只推出零碎片段**。现有 fake-kimico 只是 `read` 回显，不会触发这个场景；验收 1–10 都在假环境，无法证明真实可用。

另外，`find_overlap` 的伪代码声称避免 O(n²)，但在重复行很多时（例如 5000 行相似日志），每个候选 k 都通过末行检查，切片比较仍是 O(n²)。5000 行 / 300ms 下有性能风险。

**最佳实践方案**

先做一次真机 spike，再决定是否保留轮询架构：

1. 用真实 kimico 跑 5 分钟，记录每轮 `k` 的分布、`k=0` 比例、释放行数、实际 QQ 收到内容。若 `k=0` 频繁，轮询行差分不可行。
2. 短期缓解：在差分前屏蔽底部易变行（`ignore_tail_lines` + 正则），或把最后一行变化视为“更新”而不是“无重叠”；只在连续多轮无重叠时才重建基线。
3. 用 `difflib.SequenceMatcher` 或 LCS 做行级 diff，区分 insert / replace / delete；对 replace 只发送新增文本，不要整段丢弃。
4. 如果 TUI 使用 alternate screen 或整屏重绘，`capture-pane` 只能看到最终屏幕，行差分无法还原流式输出；此时应评估 `tmux pipe-pane` + ANSI 解析，或 `script`/PTY tee。把 capture 做成可替换适配器，不要把轮询写死。
5. 把 `find_overlap` 换成 KMP / 滚动哈希，或至少加基准测试和 5000 行上限保护。

**验证**

真实 kimico 下连续对话 20 轮，统计：手机端收到的内容与终端最终屏幕的差异；`k=0` 次数；是否有回答行丢失。此项不通过，不应冻结 v1.1。

---

## 3. P1 高风险项

| ID | 问题 | 证据 | 建议 |
|---|---|---|---|
| ARCH-01 | HTTP 多线程入队顺序不等于到达顺序，验收 3 可能失败 | `12-mod-onebot.md:9`、`01-architecture.md:9`；`ThreadingHTTPServer` 每请求一线程 | 入站加单把锁按 `time`/`message_id` 排序，或单线程 intake；补 100 并发 POST 顺序测试 |
| IO-01 | 所有 tmux 子进程都没有 timeout，tmux 卡死会挂住监控/输入线程 | `14-mod-input.md:45-51`、`15-mod-capture.md:32-40`、`17-mod-session.md:52-66` | 所有 `subprocess.run` 加 `timeout=5`，超时杀进程组并映射为对应异常 |
| THREAD-01 | 监控线程/发送线程只捕获窄异常，一次意外就静默死亡 | `18-mod-main.md:69-104`、`16-mod-mirror.md:108-129` | 每轮 try/except + 线程健康表；systemd `WatchdogSec` 或内部 watchdog |
| QUEUE-02 | `OutboundQueue.put` 的 `qsize`→丢弃→入队不是原子操作 | `16-mod-mirror.md:39-47` | 用内部锁包住整个操作，或自定义 deque + Condition |
| SEND-01 | 发送重试 `time.sleep(2)` 不可被 stop 打断；未捕获非 `OnebotAPIError` | `16-mod-mirror.md:121-124` | 改用 `stop_event.wait(2)`；每段外层 catch-all；重试前检查 generation |
| STATUS-01 | `#status` 在入站线程同步跑两次 tmux 子进程，tmux 卡住会堵住输入 | `18-mod-main.md:109-128` | 用监控线程缓存的快照；或给 tmux 调用加超时 |
| SEC-01 | 两个 token 默认都是 `change-me`，启动不拒绝；鉴权用普通 `==` | `02-config.md:16,24`、`12-mod-onebot.md:143` | 启动拒绝空/`change-me`；用 `hmac.compare_digest`；文档写轮换步骤 |
| SEC-02 | `urllib` 默认读取 `http_proxy`，回环请求可能被代理 | `12-mod-onebot.md:165` | 发送时用 `ProxyHandler({})`，或 unit 设 `no_proxy=127.0.0.1,localhost` |
| SEC-03 | 发送失败日志记录段前 80 字符，可能把终端敏感内容写进 journal | `16-mod-mirror.md:123` | 默认只记长度/hash；内容只在 DEBUG 且显式开启时记 |
| PERF-01 | `find_overlap` 最坏 O(n²)，与文档声明不符 | `15-mod-capture.md:57-77` | 基准测试；换 KMP/滚动哈希；5000 行上限 |
| UX-01 | 按行稳定、每轮释放，长回答被拆成大量小消息，1s 间隔导致队列堆积 | `15-mod-capture.md:79-118`、`16-mod-mirror.md:73-100` | 改为“静默窗口/debounce”：输出停止 300–500ms 后整批切段发送；限制每批消息数 |
| UX-02 | 用户无法发送字面 `#` 开头的文本；没有 `#help`；普通消息无 ACK | `13-mod-router.md:97-99` | 加 `##` 转义或 `/raw`；加 `#help`；可选“已注入”ACK |
| TEST-01 | 测试统一拖到 Step 8，前 7 步的“完成定义”却要求测试通过 | `40-build-order.md:55-61` | 每步先写测试；算法用 property-based（与暴力实现对比）；并发用 barrier 测试 |
| NFR-01 | 没有延迟、吞吐、CPU/内存、可用性目标；没有指标/健康检查 | 全套文档 | 定义 p95 镜像延迟、最大输出速率、队列丢弃计数；加 `/healthz` 或 `#status` 扩展 |
| OPS-01 | 没有 QQ/NapCat 风控、账号封禁、隐私与数据保留说明 | `00-overview.md`、`30-deploy.md` | 专用账号、风险告知、日志脱敏、备份/恢复步骤 |
| DEP-03 | systemd 不继承用户 PATH/HOME/TERM；`kimico` 可能来自 nvm 等用户环境 | `30-deploy.md:46-56` | 配 `Environment=PATH/HOME/TERM`；`kimico_command` 用绝对路径；启动前 `shutil.which` 预检 |
| DEP-04 | `Restart=always` 会让配置错误无限重启 | `30-deploy.md:51` | `Restart=on-failure`、`RestartPreventExitStatus=2`、`StartLimitIntervalSec/Burst` |
| STATE-01 | `StateStore.save` 无锁，输入线程与停机主线程可能同时写同一个 `.tmp` | `03-state.md:24-29`、`11-mod-state.md:17-19` | 内部锁 + `tempfile.mkstemp` 唯一临时文件；写完 `fsync` |
| SESSION-03 | tmux server 整体消失时只熔断，不重建会话 | `17-mod-session.md:104` | 区分“pane dead”与“session missing”；session missing 时重建会话（仍受退避/告警约束） |
| ACC-01 | 验收 8 改 config 后重启网关，但接管路径不会应用新 `kimico_command` | `20-acceptance.md:128`、`17-mod-session.md:64` | 测试前 kill session，或新增独立测试会话；不要依赖接管路径改配置 |
| ACC-02 | 验收 10 “显示一致”不可客观判定 | `20-acceptance.md:142-149` | 用 `capture-pane` 文本 + 固定 TERM 做 golden 对比；颜色单列 |
| DOC-01 | “空消息忽略”与“纯空白原样注入”矛盾 | `00-overview.md:58`、`14-mod-input.md:87` | 建议统一为：`text.strip()==""` 静默忽略；非空文本保留原空白注入 |
| DOC-02 | `extract_text` 对“type=text 但无 data.text”的规则表述自相矛盾 | `12-mod-onebot.md:171` | 明确：跳过该段；若没有任何有效 text 段，`has_text=False`；补测试 |
| DOC-03 | README 说模块文档固定 7 节，实际 onebot 有 9 节 | `README.md:33`、`12-mod-onebot.md` | 改成“建议 7 节，允许协议/发送等附加节”，或统一结构 |
| DOC-04 | state 缺失日志级别 INFO vs WARNING 不一致 | `03-state.md:18`、`11-mod-state.md:45` | 统一 INFO（首次启动正常） |
| DOC-05 | 验收 6 让 fake-kimico 输出 5000 行，但 `capture_lines=5000` 加提示符会截掉开头 | `20-acceptance.md:20`、`15-mod-capture.md:38` | 输出降到 3000 行，或测试配置 `capture_lines=6000` |
| DOC-06 | `set-buffer` 不带 `-b` 会覆盖用户默认粘贴缓冲区 | `14-mod-input.md:48` | 用命名 buffer：`set-buffer -b kimico-gw -- <text>`，paste 后 `delete-buffer` |
| DOC-07 | `paste-buffer` 默认把 LF 替换为 CR，多行注入行为未验证 | `14-mod-input.md:49`、`tmux` 手册 | 用 `-r` 保留 LF；在真实 kimico 上验证多行粘贴与提交 |
| DOC-08 | “网关是唯一向 kimico 注入输入的组件”与本地 attach 直接输入矛盾 | `00-overview.md:24` | 改为“网关是唯一的程序化注入组件；本地输入由 tmux 直接接管” |
| DOC-09 | “不使用锁之外的原语”但共享状态表用了 Event | `01-architecture.md:94` | 加一句“Event 仅作一次性信号，不承载数据” |
| DOC-10 | 告警文案说“连续恢复失败”，算法是滑动窗口内累计，不要求连续 | `13-mod-router.md:23`、`17-mod-session.md:80` | 改为“窗口内累计失败 N 次” |
| DOC-11 | StabilityTracker 伪代码混用 tuple 与 `.line` 属性 | `15-mod-capture.md:81,104` | 统一为 `(line, count)` 或 dataclass |
| DOC-12 | README 称“唯一依据”，但仓库还有 `docs/0001.md` 且内容不完全一致 | `README.md:3`、`docs/0001.md` | 明确 PRD 为 canonical，0001 标记为历史归档；或删除重复来源 |
| DOC-13 | 示例把 `access_token` 写成 `change-me`，容易被直接用于生产 | `02-config.md:16,24` | 示例改成 `<REQUIRED>`，并加启动校验拒绝默认值 |
| DOC-14 | curl 验收模板用字符串拼 JSON，文本含引号/换行会坏 | `20-acceptance.md:49` | 用 Python `json.dumps` 或 `jq -n` 构造请求体 |
| DOC-15 | NapCat 镜像用 `latest`，不可复现 | `30-deploy.md:82` | 固定版本号或 digest；记录升级步骤 |
| DOC-16 | state 写入没有 fsync，掉电可能丢最后一次 mirror 状态 | `03-state.md:24-29` | 写完 `f.flush(); os.fsync(f.fileno())`，再 `os.replace` |
| DOC-17 | `MirrorConfig.ignore_patterns` 是 list，frozen dataclass 并非深度不可变 | `10-mod-config.md:40-44` | 用 `tuple[str, ...]` 或返回前 `tuple(...)` |
| DOC-18 | `passes_filters` 未定义 `re.search` 还是 `re.match` | `16-mod-mirror.md:31` | 明确 `search`（默认 `^\s*$` 两者等价，但语义要写死） |
| DOC-19 | `Content-Length` 为负数时未拒绝，可能 `read(-1)` 读全部 | `12-mod-onebot.md:142` | 校验 `0 <= length <= max_body_bytes`，否则 400 |
| DOC-20 | 413 不读 body 也不关连接，keep-alive 会错位 | `12-mod-onebot.md:142` | 返回 `Connection: close`，或安全丢弃剩余 body |
| DOC-21 | `ThreadingHTTPServer` 无并发上限，本地进程可打满线程 | `12-mod-onebot.md:158` | 加信号量/固定线程池；限制 `request_queue_size` |
| DOC-22 | 回显队列在 mirror off 时只进不出，可能无限增长 | `16-mod-mirror.md:25` | `register` 时清理过期项；容量上限 50；off 时可不登记 |
| DOC-23 | 停机时先保存 state 再 join 输入线程，入站线程可能随后改 mirror | `18-mod-main.md:130-137` | 先停 intake/join 输入线程，再最终保存；或依赖 `on_change` 再写一次 |
| DOC-24 | `#status` 的 `now_wall` 参数实际未参与格式化 | `13-mod-router.md:56` | 删除参数或用于“最后输出”的相对时间 |
| DOC-25 | `12-mod-onebot.md` 的 `extract_text` 非 list 输入返回空，但接入层未校验 `message` 类型 | `12-mod-onebot.md:147` | 明确 `message` 必须是 list，否则 400；避免静默当“无文本” |
| DOC-26 | `has_text` 为 False 时用“仅支持文本消息”，但空数组也走同一路径 | `14-mod-input.md:65` | 空数组可回“消息为空”；或明确统一文案，避免歧义 |
| DOC-27 | `#mirror off` 清空队列会丢掉尚未发送的 `#status` 等回执 | `14-mod-input.md:68` | 由 P0-07 的控制 lane 解决；文档同步写明只清 bulk |
| DOC-28 | `find_overlap` 快路径 `prev == cur[:len(prev)]` 在 `len(cur) < len(prev)` 时行为正确但可读性差 | `15-mod-capture.md:65` | 加注释或改成 `len(cur) >= len(prev) and ...` |
| DOC-29 | `13-mod-router.md` 的 `#mirror on now` 判 UNKNOWN，但用户可能期望忽略多余参数 | `13-mod-router.md:103` | 保持严格可以，但文案/测试要明确；或允许尾随空白 |
| DOC-30 | `30-deploy.md` 的 `chown -R ubuntu:ubuntu /var/lib` 未覆盖 `/etc` 配置 | `30-deploy.md:30-31` | 与 P0-02 一起修；用 `install -o/-g/-m` |

---

## 4. P2 可维护性与体验项

| ID | 问题 | 建议 |
|---|---|---|
| P2-01 | 状态文件写入无 fsync | 临时文件 `flush` + `fsync` + `os.replace` |
| P2-02 | `Config` 内 list 字段破坏深度不可变 | 改用 tuple |
| P2-03 | `passes_filters` 的匹配语义未写死 | 明确 `re.search` |
| P2-04 | HTTP 未拒绝负 `Content-Length` | 加范围校验 |
| P2-05 | 413 响应未处理 keep-alive | `Connection: close` |
| P2-06 | HTTP 无并发上限 | 信号量/线程池 |
| P2-07 | `set-buffer` 覆盖默认 buffer | 命名 buffer + `-d` |
| P2-08 | `paste-buffer` 默认 LF→CR | 用 `-r` 并写真机测试 |
| P2-09 | 告警“连续失败”措辞与滑动窗口不符 | 改文案 |
| P2-10 | README 的“7 节”与 onebot 实际结构不符 | 统一或放宽 |
| P2-11 | “网关是唯一注入组件”与本地 attach 矛盾 | 改为“唯一程序化注入” |
| P2-12 | 共享状态表用 Event 但声称只用锁 | 补充 Event 语义 |
| P2-13 | StabilityTracker 伪代码 tuple/属性混用 | 统一数据结构 |
| P2-14 | `extract_text` 段落规则表述矛盾 | 重写边界条件 |
| P2-15 | curl 模板字符串拼 JSON | 用 `jq`/Python |
| P2-16 | NapCat 镜像 `latest` | 固定版本/digest |
| P2-17 | state 缺失日志级别不一致 | 统一 INFO |
| P2-18 | `#status` 的 `now_wall` 未使用 | 删除或使用 |
| P2-19 | `#status` 在输入线程同步跑 tmux | 缓存快照 |
| P2-20 | 回显队列在 off 时无限增长 | 清理 + 上限 |
| P2-21 | 停机保存 state 与输入线程竞争 | 调整顺序 |
| P2-22 | `set-buffer`/`paste-buffer` 未清理 buffer | `delete-buffer` |
| P2-23 | `find_overlap` 快路径可读性 | 加长度判断 |
| P2-24 | 未知 `#` 指令的尾随参数策略 | 明确严格/宽松 |
| P2-25 | `30-deploy.md` 未覆盖 `/etc` 属主 | 用 `install` 一次到位 |

---

## 5. 架构级建议（需要你决定是否解冻）

这些不是“文档修一修”能解决的；建议在 v1.1 中单独立项，不混在 P0 修复里。

### L2-01 把 capture 做成可替换适配器，先验证真实 kimico

当前把“300ms 轮询 + 行重叠差分 + 逐行稳定”写死为唯一方案。真实 kimico 的 TUI 可能让这套方案失效（见 P0-09）。建议：

- 定义 `CaptureSource` 接口：`snapshot() -> list[str]` 或 `stream() -> Iterator[OutputEvent]`。
- 先实现现有 polling 版本，再做一个 `pipe-pane` 版本；用同一组验收对比。
- 如果 polling 的 `k=0` 比例高，或输出丢失，切换到 `pipe-pane` + ANSI 解析；不要在没有真机数据时冻结。

### L2-02 输出通道改为“优先级 + 批处理”

当前“逐行稳定 + 每段 1s + 单个有损队列”会造成：

- 长回答被拆成很多小消息，手机上像刷屏；
- 输出洪峰时队列堆积/丢最旧；
- 控制消息被埋在后面。

建议：

- 两条 lane（控制/批量），控制优先（P0-07）。
- 以“静默窗口”批处理：输出停止 300–500ms 后，把整批稳定行按 1500 字符切段，一次发送；而不是每轮稳定都发。
- 给批量 lane 加自适应速率：队列长度超过阈值时，合并相邻段或降低发送频率，避免丢内容。
- 在 `#status` 中暴露 `dropped_bulk`、`capture_k0`、`send_failures` 等计数。

### L2-03 监控线程改为单写者命令循环

当前 `Event` + 共享队列 + 多线程直接改状态，导致 `#clear`/`#mirror off` 竞态。建议：

- 所有会改变输出状态的动作（clear、mirror off、respawn、baseline reset）都变成监控线程队列里的命令。
- 入站 worker 只发命令、等 ACK；不在自己的线程里直接动 pipeline/queue。
- 这样“谁拥有 baseline/tracker/generation”只有一个答案，竞态从设计上消失。

### L2-04 部署生命周期与网关解耦

见 P0-01。把 tmux server 作为独立生命周期管理，网关只负责 attach/接管；部署手册明确“先有 tmux，后有网关”。这比在 unit 里加 `KillMode=process` 更干净。

### L2-05 文档治理：单一事实源 + 变更记录

当前 17 份文档大量重复（指令表、文案、恢复参数、线程模型），已经出现漂移。建议：

- 选一份 normative spec（建议 `00-overview.md` + `01-architecture.md`）作为唯一事实源；其他文档只做模块细化，不重复定义常量。
- 固定文案、配置默认值、线程模型各自只出现一次；其他位置引用。
- 加 `docs/prd/CHANGELOG.md` 和 ADR（每个冻结决策一条：背景、选项、结论、影响）。
- 加一个轻量一致性检查脚本：扫描重复文案/默认值/接口签名，CI 或 pre-commit 跑。
- 明确冲突优先级：例如 `00 > 01 > 02/03 > 1x > 20 > 30 > 40`，避免“都自称唯一依据”。

---

## 6. 建议的修复顺序

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| 第 1 步 | P0-01～P0-05：部署生命周期、配置权限、history-limit、takeover、恢复三态/计数 | 验收 8、9 在“已有会话”和“网关重启”两种场景通过 |
| 第 2 步 | P0-06～P0-08：clear/off 竞态、控制 lane、回显行级过滤 | 并发 clear/off 100 次无旧输出；洪峰下 #status/告警必达 |
| 第 3 步 | P0-09：真实 kimico spike + 差分加固 | 20 轮真实对话无回答丢失；`k=0` 比例可接受 |
| 第 4 步 | P1：timeout、线程监督、安全、UX 批处理、测试策略 | P1 表逐项关闭或有明确豁免 |
| 第 5 步 | P2 + 文档治理 | 一致性检查通过，v1.1 冻结 |

---

## 7. 必须补充的验收

现有 10 条验收覆盖了主路径，但漏掉了最容易出事故的场景。建议至少加：

1. **部署重启**：`systemctl restart` 后 tmux/kimico PID 不变。
2. **配置权限**：`sudo -u <service-user> test -r /etc/.../config.toml`。
3. **历史容量**：输出 3000 行后 `capture-pane -S -5000` 行数 > 2000。
4. **恢复边界**：启动即 dead 第一次等 5s；respawn 失败只计 1 次；tmux 命令超时不被当成 dead。
5. **并发清理**：持续输出时 100 次 `#clear`，每次回执后无旧 generation 段；`#mirror off` 同理。
6. **队列优先级**：bulk 灌满 500 段后 `#status` 2s 内到达；`#mirror off` 不清掉告警。
7. **回显边界**：回显+多行回答同块、提示符前缀、连续两条相同文本。
8. **真实 kimico**：5–10 分钟对话，统计输出丢失、重复、延迟、`k=0`。
9. **并发入站**：100 个并发 POST，注入顺序与 message_id/time 顺序一致。
10. **安全**：错误 token 401；默认 token 启动失败；负 Content-Length 400；超限 413。
11. **子进程卡死**：模拟 tmux 不响应，网关在 5s 内超时并记录，不永久挂起。
12. **线程异常**：注入一次未捕获异常到监控/发送线程，线程仍存活或 systemd 能拉起。

---

## 8. 附：本地事实核验记录

| 核验 | 命令/来源 | 结果 |
|---|---|---|
| tmux 版本 | `tmux -V` | `tmux 3.4` |
| history-limit 语义 | `man tmux` | “applies only to new windows - existing window histories are not resized” |
| history-limit 实测 | 先建旧 pane，再 `set-option -g history-limit 50000`，再建新 pane；各输出 4000 行 | 旧 pane `capture-pane -S -5000` 约 2001 行；新 pane 约 4001 行 |
| systemd 默认 KillMode | `man systemd.kill` | 默认 `control-group`；unit stop 时杀掉 cgroup 内所有剩余进程 |
| set-buffer 默认 buffer | `man tmux` | 不指定 `-b` 时操作默认 buffer |
| paste-buffer 换行 | `man tmux` | 默认把 LF 替换为 separator（默认 CR）；`-r` 保留 LF |
| Python 版本 | `python3 --version` | `Python 3.12.3` |

**未验证项（需真机）**：真实 kimico 的 TUI 重绘频率、提示符格式、多行粘贴行为、NapCat 对空 body 的接受度、QQ 风控实际限速。这些不应在文档里写成“已确定”。

---

## 9. 最终建议

这份 PRD 的模块拆分、术语表和验收意识都不错，问题主要集中在**把未验证的假设写成了冻结事实**：tmux 生命周期、history-limit、TUI 差分、回显匹配、队列语义。建议不要直接进入编码，而是：

1. 用 1–2 天修完 9 个 P0，并把上面的 12 条验收补进 `20-acceptance.md`。
2. 用真实 kimico 做一次 10 分钟 spike，拿到 `k=0` 比例、输出丢失率、延迟数据。
3. 根据 spike 结果决定是否保留轮询架构；如果保留，至少加 volatile tail 屏蔽和行级回显。
4. 再冻结 v1.1，作为 AI 实现的唯一依据。

如果只能做一件事：先修 P0-01（systemd cgroup）和 P0-05（恢复误杀），因为它们会直接丢掉 kimico 的上下文；其次是 P0-07（控制 lane），因为它决定用户在故障时能不能收到消息。
