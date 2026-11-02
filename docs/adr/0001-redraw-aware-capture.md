# ADR-0001: 重绘感知的捕获差分

状态：accepted
日期：2026-11-02

## 背景

Step 0 用真实 `kimi 2.4.1` TUI 验证 `15-mod-capture.md` 的 v1.1 捕获方案。v1.1 以 `find_overlap`（prev 后缀 = cur 前缀）为主路径、`recover_new_lines`（仅 insert）为 k=0 恢复。

实测结果：5.4 分钟、210 条带唯一标签的长回答行全部出现在最终画面，但 **0 条被送达**；正常流式回答期间基线重建 15 次。根因是 kimi TUI 在流式输出时**原地改写最后一条未完成的行并向下追加**，快照不是 append-only：

```text
prev: ... SPIKE-H-024
cur : ... SPIKE-H-024 hotel
cp  : 49（之前所有行完全一致）
```

`find_overlap` 返回 0；`recover_new_lines` 只看到 `replace`，于是把回答当成重绘噪声丢弃。`mask_tail(1/2)` 无法改善（k=0 比例与 mask 0 相同），因为不匹配发生在快照顶部/前缀，不在底部易变行。

同时发现 `capture-pane -t =<session>` 在 tmux 3.4 下报 `can't find pane`；`-t =<session>:` 才可用。

## 决策

1. 捕获差分的**主路径**改为“稳定公共前缀 + 活尾同步”：
   - `cp = common_prefix(prev, cur)`；
   - `cur[:cp]` 为稳定前缀，`cur[cp:]` 为活尾；
   - 跟踪器保留“未释放的稳定前缀行 + 当前活尾”，每轮把旧活尾替换为新的 `cur[cp:]`，位置 `< cp` 且内容不变的行计数 +1；
   - 释放仍按 `stable_rounds`，块内最后一条非空行需 `stable_rounds + 1` 轮。
2. 半行语义：上一轮未完成的行在下一轮被完成版**替换**，不能同时输出半行与完成行。
3. 回退路径保留：
   - `cp` 塌缩但 KMP `k>0`：捕获窗口滑动，按 `len(prev)-k` 调整偏移后同步；
   - `k=0`：`recover_new_lines` 仅作最后手段，连续失败达阈值才重建基线。
4. `capture-pane` 目标固定为 `=<session>:`。

决策由真实数据支撑：重绘感知方案在 1083 轮、5.4 分钟内送达 210/210 行，0 丢失，p95 延迟 0.601s；离线帧回放 60/60、0 重复。

## 影响

- `docs/prd/15-mod-capture.md` §4/§6 的差分与稳定器契约由本 ADR 取代；§3 的 capture 目标改为 `=<session>:`。其余章节仍有效。
- `capture.py` 的公共 API 需要新增/调整跟踪器的同步语义（`sync(cur, cp)` 或等价接口）；`find_overlap`、`mask_tail`、`recover_new_lines` 保留。
- ticket 10 的测试必须覆盖：半行完成替换、状态栏/围栏churn、窗口滑动、`recover` 回退、60 行以上流式回答零丢行零重复、真实 tmux 的 `=<session>:` 目标。
- 监控线程每轮计算 `cp`；`k`/`recover` 只在回退路径使用。
- 已知风险：TUI 的思考文本、状态栏、代码围栏会进入释放流，需要 mirror 的过滤/回显抑制处理；5000 行窗口滑动只有单测，尚无真实长会话验证。

## 参考

- `docs/output/spike/kimico-capture.md`
- `docs/output/spike/run-v2/summary.json`
- `docs/output/spike/frames/frames.jsonl`
- `docs/prd/40-build-order.md` Step 0
