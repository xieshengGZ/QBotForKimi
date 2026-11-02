# 03 · 状态文件（state.json）

## 1. 内容

```json
{
  "mirror": false
}
```

- 仅持久化 `mirror` 一个字段（bool）。
- **不**持久化输出基线、队列内容、恢复计数；网关重启后输出基线重置到当前屏幕，不回放历史内容。

## 2. 读写时机

| 时机 | 操作 |
|---|---|
| 网关启动 | 读取；文件缺失、JSON 损坏、`mirror` 类型非 bool → 按 `mirror=false` 启动并记 warning |
| `#mirror on` / `#mirror off` 生效时 | 立即写入新值 |
| 网关收到 SIGTERM/SIGINT 正常退出时 | 再写入一次当前值 |

其他时刻不读写该文件。读取到的未知键忽略（向前兼容）。

## 3. 原子写入

1. 若父目录不存在，先创建（`os.makedirs(..., exist_ok=True)`）。
2. 写入同目录临时文件（如 `state.json.tmp`），内容 `json.dumps({"mirror": value})`。
3. `os.replace(tmp, path)` 原子替换。

写入失败（权限、磁盘等）：记 error 日志，**不中断运行**——内存中的 mirror 状态仍然生效，仅失去重启后恢复能力。

## 4. 损坏容错示例

| 文件内容 | 加载结果 |
|---|---|
| 文件不存在 | `mirror=false`，记 warning（首次启动属正常，可为 info） |
| `{"mirror": true}` | `mirror=true` |
| 空文件 / 非 JSON | `mirror=false`，记 warning |
| `{"mirror": "yes"}` | `mirror=false`，记 warning |
| `{}` | `mirror=false`（缺键按默认），不记 warning |
| `{"mirror": true, "x": 1}` | `mirror=true`，忽略 `x` |

接口与实现要求见 `11-mod-state.md`。
