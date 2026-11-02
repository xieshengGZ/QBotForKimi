# 02 · 配置（config.toml）

## 1. 加载方式

- 启动命令：`python3 -m kimico_gateway [config_path]`；未传参时默认路径 `/etc/kimico-gateway/config.toml`。
- 使用 `tomllib` 解析；解析失败、缺必需键、类型或取值非法 → 启动失败（见 `10-mod-config.md`）。
- 配置中未出现的**未知键**：记 warning 日志并忽略，不影响启动。

## 2. 完整示例（默认值即示例值）

```toml
[server]
host = "127.0.0.1"
port = 8080
path = "/cq"
access_token = "change-me"        # 必需，无默认
max_body_bytes = 1048576

[auth]
main_qq = 1000000000              # 必需，无默认

[onebot]
api_base = "http://127.0.0.1:3000"
access_token = "change-me"        # 必需，无默认

[tmux]
session = "kimico"
kimico_command = "kimico"
history_limit = 50000
capture_lines = 5000
poll_ms = 300
stable_rounds = 3

[mirror]
chunk_chars = 1500
send_interval_ms = 1000
outbound_queue_max = 500
ignore_patterns = ["^\\s*$"]

[recovery]
backoff_seconds = [5, 15, 60]
max_attempts = 3
window_seconds = 600
success_reset_seconds = 60

[state]
path = "/var/lib/kimico-gateway/state.json"

[logging]
level = "INFO"
```

## 3. 字段定义

### [server] — HTTP 接入

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| host | str | 默认 `"127.0.0.1"` | 非空 | onebot |
| port | int | 默认 `8080` | 1–65535 | onebot |
| path | str | 默认 `"/cq"` | 以 `/` 开头 | onebot |
| access_token | str | **必需** | 非空 | onebot |
| max_body_bytes | int | 默认 `1048576` | ≥ 1 | onebot |

### [auth]

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| main_qq | int | **必需** | > 0 | onebot、mirror（发送目标） |

### [onebot] — 出站 API

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| api_base | str | 默认 `"http://127.0.0.1:3000"` | 以 `http://` 或 `https://` 开头 | onebot、mirror |
| access_token | str | **必需** | 非空 | onebot、mirror |

### [tmux]

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| session | str | 默认 `"kimico"` | 非空 | session、capture、input |
| kimico_command | str | 默认 `"kimico"` | 非空 | session |
| history_limit | int | 默认 `50000` | ≥ 1000 | session |
| capture_lines | int | 默认 `5000` | ≥ 100 | capture |
| poll_ms | int | 默认 `300` | ≥ 50 | __main__（监控节奏） |
| stable_rounds | int | 默认 `3` | ≥ 1 | capture |

### [mirror]

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| chunk_chars | int | 默认 `1500` | ≥ 100 | mirror |
| send_interval_ms | int | 默认 `1000` | ≥ 0 | mirror |
| outbound_queue_max | int | 默认 `500` | ≥ 10 | mirror |
| ignore_patterns | list[str] | 默认 `["^\\s*$"]` | 每项须为可编译正则 | mirror |

### [recovery]

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| backoff_seconds | list[int] | 默认 `[5, 15, 60]` | 非空，每项 ≥ 0 | session |
| max_attempts | int | 默认 `3` | ≥ 1 | session |
| window_seconds | int | 默认 `600` | ≥ 1 | session |
| success_reset_seconds | int | 默认 `60` | ≥ 1 | session |

### [state]

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| path | str | 默认 `"/var/lib/kimico-gateway/state.json"` | 非空绝对路径 | state |

### [logging]

| 字段 | 类型 | 必需/默认 | 校验 | 消费方 |
|---|---|---|---|---|
| level | str | 默认 `"INFO"` | `DEBUG/INFO/WARNING/ERROR`（不区分大小写） | __main__ |

## 4. 刻意不可配置的常量

以下值是冻结行为的一部分，**作为代码常量**实现，不进入 config：

| 常量 | 值 | 所在模块 |
|---|---|---|
| 入站队列容量 | 50 | input |
| message_id 去重表容量 | 200 | onebot |
| 出站发送超时 | 3 s | onebot（函数参数默认值） |
| 发送失败重试 | 等待 2 s，重试 1 次，仍失败则丢弃 | mirror（发送循环） |
| 回显抵消窗口 | 10 s | mirror |
| 空消息/文案 | 见 `13-mod-router.md` 文案表 | router |

## 5. 配置变更语义

配置仅在启动时读取一次；运行期修改 config.toml 不生效，需重启网关。state.json 中的 mirror 为运行期状态，不由 config 管理（见 `03-state.md`）。
