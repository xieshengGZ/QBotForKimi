# PRD 变更记录

## v1.1（2026-11-02）

本版根据 `docs/output/prd-review.md` 的优化方案修订，目标是让 PRD 可以作为 AI 实现的唯一依据，并修复会导致核心承诺失效的设计缺陷。

### 冻结决策的变更

| 编号 | 变更 | 原因 |
|---|---|---|
| C-01 | tmux pane 检测由 `bool is_pane_dead` 改为四态 `PaneState`（ALIVE/DEAD/MISSING/UNKNOWN）；仅 DEAD/MISSING 触发恢复，UNKNOWN 不动作 | 避免 tmux 瞬时故障被误判为 dead，`respawn-pane -k` 杀掉活着的 kimico |
| C-02 | 恢复计数改为“一次尝试只结算一次”；启动即 dead 不计失败 | 修复 respawn 失败被重复计数、首次退避从 15s 开始的问题 |
| C-03 | 会话创建先设置 `history-limit` 再 `new-session`；接管路径幂等设置 `remain-on-exit on` | tmux 选项只对新窗口生效；已有会话没有 remain-on-exit 会让恢复失效 |
| C-04 | 出站拆为控制 lane + 输出 lane；控制/告警永不参与输出溢出丢弃 | “控制回执始终发送”和熔断告警不能被有损队列破坏 |
| C-05 | 引入 `OutputEpoch`（generation）；`#clear`/`#mirror off` 递增 epoch，发送前校验，旧段丢弃 | 修复清空后监控线程仍入队旧输出的竞态 |
| C-06 | 回显抵消改为行级、保守匹配；不再要求整块完全相等 | 多行回答/提示符前缀下，整块匹配会重复回显或吞掉回答 |
| C-07 | 捕获差分改为 KMP 线性算法；`k=0` 先尝试恢复插入行，连续多次才重建基线；增加真实 kimico spike 门禁 | 避免 TUI 底部状态行导致每轮 `k=0` 和输出丢失；避免 O(n²) |
| C-08 | 输出增加静默窗口批处理（默认 300ms）与批量上限 | 避免长回答被拆成大量小消息、队列堆积和刷屏 |
| C-09 | systemd unit 使用 `KillMode=process`、`Restart=on-failure`、`RestartPreventExitStatus=2`、`StartLimit*`，并修正 config 权限与 PATH/HOME/TERM | 防止 `systemctl restart` 连带杀掉 tmux/kimico；防止配置错误无限重启；保证服务用户能读配置 |
| C-10 | 所有 tmux 子进程增加超时；监控/发送线程增加逐轮异常隔离与主线程健康检查 | tmux 卡死或单次异常不能永久拖死网关 |

### 契约与文档修订

- `#status` 改为 8 行：入站队列、控制队列、输出队列分开统计。
- 术语澄清：区分 `tmux 会话` 与 `Kimi 会话`；恢复统称下区分 `respawn`（DEAD）与 `recreate`（MISSING）；控制 lane 明确为“入队保证、投递尽力而为”；新增根目录 `GLOSSARY.md`。
- `OutboundQueue` 改为 `OutboundBus`；`MirrorSwitch` 保留，新增 `OutputEpoch`。
- 新增配置：`mirror.batch_quiet_ms`、`mirror.batch_max_chars`、`mirror.echo_prefix_patterns`、`tmux.volatile_tail_lines`、`tmux.k0_rebuild_threshold`。
- HTTP 接入改为单线程 `HTTPServer`，天然保证事件入队顺序；去掉多请求线程乱序风险。
- 明确纯空白消息静默忽略；`##` 前缀用于发送字面 `#` 文本。
- 统一 state 文件缺失日志为 INFO；state 写入增加锁、唯一临时文件和 fsync。
- 修正 `extract_text` 边界、Content-Length 负数、413 keep-alive、proxy 环境、日志脱敏等 P1/P2 项。
- 验收补充部署重启、配置权限、历史容量、并发清理、控制 lane、回显边界、真实 kimico、并发入站、安全、子进程卡死、线程异常等场景。

### 未纳入 v1.1

- 用 `tmux pipe-pane` 替换轮询捕获：作为 v1.2 候选，需先完成真实 kimico spike；v1.1 先把轮询差分做成可替换适配器并加恢复策略。
- 多会话、多用户、群聊、图片/文件：仍为非目标。
- 完整指标系统/HTTP 健康端点：v1.1 只要求结构化日志与 `#status` 计数；独立指标端点列入 v1.2。

### 兼容性

- v1.1 不兼容 v1.0 的 `is_pane_dead`、`OutboundQueue`、`StatusSnapshot` 接口；实现者以 v1.1 文档为准。
- `state.json` 格式不变，仍只存 `mirror`。
- `config.toml` 新增字段均有默认值；旧配置可直接启动。`mirror.batch_quiet_ms` 默认 300，`mirror.batch_max_chars` 默认 3000，`mirror.echo_prefix_patterns` 默认 `["^\\s*[>›]\\s*"]`，`tmux.volatile_tail_lines` 默认 0，`tmux.k0_rebuild_threshold` 默认 3。

---

## v1.0

初始冻结版本，见 `docs/output/prd-v1.0-backup/`。
