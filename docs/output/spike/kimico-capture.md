# Step 0：真实 kimico TUI 捕获门禁

日期：2026-11-02
结论：**通过（v1.2 重绘感知差分）**

历史：v1.1 的 append-only 差分首次门禁 **不通过**（210/210 回答行丢失）。本文件保留失败证据，并记录修正算法与通过结果。

## 1. 判定

| 门禁标准 | v1.1 结果 | v1.2 结果 |
|---|---|---|
| `k=0` 比例 < 10% | 6.30% | 主路径不再依赖 `k`；5 次 recover 回退，0 次 slide/reset |
| 回答行丢失为 0 | **210/210 丢失** | **210/210 送达，丢失 0** |
| p95 延迟 ≤ 2s | 无法计算（0 行送达） | **p95 0.601s，max 0.901s** |
| 多行粘贴正确、不重复提交 | 通过（首次） | 通过（独立复测） |
| 采样错误 | 0 | 0 |

结论：按 `docs/prd/40-build-order.md` 的“通过”标准，v1.2 满足 Step 0，可以进入 Step 1。

## 2. 环境与运行

- Linux，`tmux 3.4`，`Python 3.12.3`，`kimi 2.4.1`，模型 `DeepSeek Flash`。
- 隔离 tmux server：`tmux -L kimico-spike`，pane `120x40`，`history-limit 50000`，`extended-keys on`，工作目录 `/tmp/kimico-spike-work`。
- 采样：每 300ms 执行
  `tmux -L kimico-spike capture-pane -p -J -S -5000 -t =kimico:`
- 正式运行：**325.06 秒（5.4 分钟）**，1083 轮，capture 错误 0。
- 交互：长回答 3 个（80/60/70 行，带 `SPIKE-A/C/E-XXX` 唯一标签）、短回答 3 个、1 次 bracketed 多行粘贴；本地输入通过 `tmux send-keys` 发送。

### 2.1 PRD 命令修正

`15-mod-capture.md` §3 的 `-t =<session>` 在 `capture-pane` 下不可用：

```text
$ tmux -L kimico-spike capture-pane -p -J -S -5000 -t =kimico
can't find pane: =kimico
$ tmux -L kimico-spike capture-pane -p -J -S -5000 -t =kimico:
<正常输出>
```

`=<session>:`（或纯 session 名）可用。**ticket 10 的实现与 `test_capture_command` 必须使用 `=<session>:`**。

## 3. v1.1 失败证据（保留）

v1.1 按 `15-mod-capture.md` 的 `find_overlap`（prev 后缀 = cur 前缀）+ `recover_new_lines`（仅 insert）重放：

- `k=0` 比例 6.30%，但 210 条带标签回答行 **0 条送达**。
- 68 个 k=0 轮中只有 14 个 recover 非空（20.6%），回答行恢复 0 行。
- 正常流式回答期间基线重建 **15 次**。
- 最终画面 210/210 标签齐全，说明 `capture-pane` 抓取完整。

根因：kimi TUI 在流式回答时**原地改写最后一条未完成的行并向下追加**，快照不是 append-only。示例（完整帧，非截断）：

```text
prev: ... SPIKE-H-024
cur : ... SPIKE-H-024 hotel
cp  : 49（之前所有行完全一致）
```

`find_overlap` 的“prev 后缀 = cur 前缀”假设不成立，返回 0；`recover_new_lines` 只看到 `replace`，于是回答行被当成“重绘噪声”丢弃。`mask_tail(1/2)` 的 k=0 比例与 mask 0 完全相同，屏蔽底部易变行无济于事。

## 4. v1.2 算法：稳定前缀 + 活尾同步

核心不再是“整个 prev 是 cur 的前缀/后缀”，而是：

1. 计算 `cp` = `prev` 与 `cur` 的**最长公共前缀**。流式回答时 `cp` 单调增长（真实样本 21→35→49→64→79）。
2. `cur[:cp]` 是已稳定的转录前缀；`cur[cp:]` 是活尾（可能包含未完成的行、状态栏、输入框）。
3. 跟踪器保留“尚未释放的稳定前缀行 + 当前活尾”；每轮：
   - 位置 `< cp` 且内容仍一致的 pending 行计数 +1；
   - 旧活尾丢弃，换成新的 `cur[cp:]`（因此上一轮的半行被完成版**替换**，不会重复）；
   - 按 `stable_rounds` 释放前缀；块内最后一条非空行需要 `stable_rounds + 1` 轮。
4. 回退路径：
   - `cp` 塌缩但 KMP overlap `k>0`：捕获窗口滑动，按 `k` 调整偏移后同步；
   - `k=0`：`recover_new_lines` 只作最后手段，连续失败达阈值才重建基线。

伪代码：

```python
cp = common_prefix(prev, cur)
if cp >= 5 and (not pending or cp >= start):
    released = tracker.sync(cur, cp)        # 主路径
elif k > 0:
    tracker.shift(len(prev) - k)
    released = tracker.sync(cur, k)         # 窗口滑动
else:
    recover_new_lines(prev, cur)            # 最后手段
```

## 5. v1.2 结果

| 指标 | 实测 |
|---|---|
| 回答行送达 | **210/210**（A 80、C 60、E 70），丢失 0 |
| 回答行延迟 | p50 0.600s，p95 0.601s，max 0.901s |
| 主路径同步 | 1078 轮 |
| 回退 | recover 5，slide 0，reset 0 |
| `cp` | p50 137，p95 281，max 281 |
| 释放总行数 | 374（含 TUI 状态/围栏等 164 行非标签内容） |
| 处理耗时 | p50 3.65ms，p95 5.46ms，max 12.99ms |
| capture 耗时 | p50 3.38ms，p95 5.22ms，max 12.69ms |
| 轮询间隔 | p50 300ms，p95 301ms，max 303ms |
| capture 错误 | 0 |

离线回放同一段真实帧（60 行回答，`docs/output/spike/frames/`）：60/60 标签送达、0 重复，`stable_rounds=3` 时 p95 0.608s。

## 6. 多行粘贴

独立复测使用规约命令：

```bash
tmux load-buffer -b spike2 /tmp/kimico-spike-paste2.txt
tmux paste-buffer -p -r -b spike2 -t kimico
```

- 粘贴后输入框完整显示 3 行：`PASTE-ALPHA/BETA/GAMMA`；
- 只按一次 Enter，用户消息回显 1 次；
- 回答把三行各重复一次，画面中每个 `PASTE-*` 出现 2 次（用户回显 + 回答），没有多次提交。

说明：正式长跑里的驱动脚本在 TUI 尚未真正空闲时发送粘贴，输入框未定位、未插入；该次数据作废，以上独立复测是门禁依据。

## 7. 结论与后续

- **Step 0 通过**：回答行零丢失、p95 延迟 0.601s、粘贴正确、capture 零错误。
- 决策记录：`docs/adr/0001-redraw-aware-capture.md`。`15-mod-capture.md` 的 §4/§6 差分与稳定器契约由该 ADR 取代；§3 的 capture 目标改为 `=<session>:`。
- ticket 10 的实现与验收必须包含：
  - 真实 tmux 下 `=<session>:` 目标；
  - 流式长回答（≥60 行唯一标签）零丢行、零重复；
  - 半行完成的替换语义（不得同时输出半行与完成行）；
  - 窗口滑动与 `recover_new_lines` 回退的单测；
  - 正常流式回答期间不触发基线重建。
- 可以开 Step 1 的 W1：ticket 01（当前唯一候选）。

## 8. 产物与限制

- `docs/output/spike/spike_capture_v2.py` — v1.2 在线采样器（重绘感知）
- `docs/output/spike/spike_capture.py` — v1.1 采样器（失败基线）
- `docs/output/spike/drive_spike.py` — 真实 TUI 交互驱动
- `docs/output/spike/probe_frames.py` / `probe_stream.py` — 帧与 pipe-pane 探针
- `docs/output/spike/replay_frames.py` — 离线帧回放
- `docs/output/spike/run-v2/` — v1.2 汇总、1083 轮指标、驱动事件
- `docs/output/spike/run/` — v1.1 失败汇总与证据
- `docs/output/spike/frames/` — 真实流式帧

限制：

- 只测了一个模型（DeepSeek Flash）、一个 pane 尺寸（120x40）、kimi 2.4.1 + tmux 3.4；不同模型/尺寸的重绘细节可能不同。
- 5.4 分钟未触发 5000 行捕获窗口滑动；`slide` 回退只有单测，没有真实长会话验证。
- TUI 的思考文本、状态栏、代码围栏也进入释放流（374 行中的 164 行）；mirror 的过滤/回显抑制仍需在 Step 4 处理。
- 粘贴复测与正式长跑分开执行；正式长跑中的粘贴驱动时序问题不影响粘贴本身的结论。
