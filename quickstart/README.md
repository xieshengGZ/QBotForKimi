# quickstart：单文件精简版

一个只依赖 Python 标准库的单文件桥接器，适合“先把链路跑通”：

```text
tmux/kimi  <--  kimico_quickstart.py  -->  NapCat  <--  手机 QQ
```

它复用完整网关生成的运行配置（`/var/lib/kimico/config.toml`），但**不替代**
`kimico_gateway/`。长期运行、systemd、恢复、双 lane、状态持久化请用完整网关。

## 运行

```bash
# 直接复用已安装服务生成的配置
python3 quickstart/kimico_quickstart.py \
  --config /var/lib/kimico/config.toml

# 或显式指定关键参数
python3 quickstart/kimico_quickstart.py \
  --session kimico \
  --kimico /home/ubuntu/.kimi-code/bin/kimi \
  --main-qq 123456789 \
  --server-token '<上报 token>' \
  --onebot-token '<API token>' \
  --port 8080
```

启动后：

- 如果 tmux 会话不存在，会在会话里启动 `--kimico` 指定的命令；
- 监听 `127.0.0.1:8080/cq`，只接受主 QQ 的私聊；
- 默认 mirror 为 on；加 `--mirror-off` 可以从关闭状态启动；
- Ctrl-C 或 SIGTERM 停止脚本，**不会**杀掉 tmux/kimi。

配置最少需要：

```toml
[server]
access_token = "<NapCat 上报 token>"

[auth]
main_qq = 123456789

[onebot]
api_base = "http://127.0.0.1:3000"
access_token = "<NapCat API token>"

[tmux]
session = "kimico"
kimico_command = "/home/ubuntu/.kimi-code/bin/kimi"
```

NapCat 部署与 OneBot 配置见 `deploy/README.md` 第 6 节。

## 指令

| 发送 | 效果 |
|---|---|
| `#status` | 6 行简化状态（tmux/kimi、mirror、待发送行、最后输出、运行时长） |
| `#mirror on` / `#mirror off` | 开启/关闭输出推送；off 会丢弃待发送批次 |
| `#clear` | 清空待发送批次并重置抓屏基线 |
| 普通文本 | 原样注入 kimi 并回车 |
| `##文本` | 注入字面量 `#文本` |

未知 `#` 指令返回用法提示；其他账号、群消息静默忽略。

## 与完整网关的差异

| 能力 | quickstart | 完整网关 |
|---|---|---|
| 单文件、零依赖 | 是 | 否（多模块） |
| kimi 崩溃自动重启 | 否 | 是 |
| 状态持久化（mirror 重启恢复） | 否 | 是 |
| 控制 lane 优先于输出 lane | 简单优先队列 | 是（带溢出策略） |
| 抓屏差分 | 稳定公共前缀，滚动时可能丢行 | 重绘感知 + K 回退 + 恢复 |
| 回显抑制 | 行级、10 秒窗口 | 行级、10 秒窗口、可配置前缀 |
| 并发入站 | 单线程 HTTP，失败即丢 | 同左 + 50 条队列 + 去重 |
| 批量/切段 | 300ms 静默、1500 字符 | 同左 + 1500 字符硬切 |
| systemd/部署 | 自己写 unit | 提供 `deploy/` |

## 已知限制

- 滚动或屏幕重绘导致公共前缀回退时，滚动出去的行可能丢失；不会重复发送。
- 长提示软换行时，续行可能仍被镜像（完整网关也有此限制）。
- 没有消息队列上限和 recovery；NapCat/网络失败时只重试一次。
- 只支持一个主 QQ、私聊、文本。
- 不读取 state.json；mirror 每次启动按 `--mirror-off`/默认值决定。

## 测试

```bash
/home/ubuntu/qq/.venv/bin/python -m pytest tests/test_quickstart.py -q
```
