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
| 网关启动 | 读取；文件缺失 → `mirror=false` + INFO；JSON 损坏或 `mirror` 类型非 bool → `mirror=false` + WARNING |
| `#mirror on` / `#mirror off` 生效时 | 立即写入新值 |
| 网关收到 SIGTERM/SIGINT 正常退出时 | 在入站线程停止后再写入一次当前值 |

其他时刻不读写该文件。读取到的未知键忽略（向前兼容）。

## 3. 原子写入

1. 若父目录不存在，先创建（`os.makedirs(..., exist_ok=True)`）。
2. 在目标目录用 `tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=dirname)` 创建唯一临时文件，避免并发写同一个 `.tmp`。
3. 写入 `json.dumps({"mirror": value})`；`flush()` 后 `os.fsync(fileno)`。
4. `os.close(fd)` 后 `os.replace(tmp, path)` 原子替换。
5. 任一步失败：清理临时文件，记 error 日志，不中断运行。

`StateStore.save` 内部使用 `threading.Lock` 串行化；入站线程与停机主线程不会同时写同一个临时文件。

## 4. 损坏容错示例

| 文件内容 | 加载结果 |
|---|---|
| 文件不存在 | `mirror=false`，记 INFO（首次启动属正常） |
| `{"mirror": true}` | `mirror=true` |
| 空文件 / 非 JSON | `mirror=false`，记 warning |
| `{"mirror": "yes"}` | `mirror=false`，记 warning |
| `{}` | `mirror=false`（缺键按默认），不记 warning |
| `{"mirror": true, "x": 1}` | `mirror=true`，忽略 `x` |

接口与实现要求见 `11-mod-state.md`。
