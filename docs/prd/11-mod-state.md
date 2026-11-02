# 11 · 模块规约：state.py

## 1. 职责

读写状态文件 `state.json`（仅 `mirror` 一个布尔字段）。规则见 `03-state.md`。

## 2. 接口签名

```python
class StateStore:
    def __init__(self, path: str) -> None: ...

    def load(self) -> bool:
        """读取 mirror 状态。文件缺失、损坏、类型非法时返回 False。
        文件缺失记 INFO；损坏/类型非法记 warning。"""

    def save(self, mirror: bool) -> None:
        """原子写入。失败时记 error 日志，不抛异常。线程安全。"""
```

## 3. 行为规约

1. `load` 按 `03-state.md` 第 4 节表格逐行实现；`json.JSONDecodeError`、非 dict 顶层、`mirror` 非 bool 均归“损坏”处理（返回 False + warning）。
2. `save`：
   - 获取内部 `threading.Lock`；
   - `os.makedirs(dirname, exist_ok=True)`；
   - 用 `tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=dirname)` 创建唯一临时文件；
   - 写入 `json.dumps({"mirror": mirror})`，`flush()` + `os.fsync(fd)`；
   - `os.close(fd)` 后 `os.replace(tmp, path)`；
   - 任一步 `OSError` → 清理临时文件，记 error 日志后返回，不抛出。
3. 模块不缓存状态；`load` 每次读盘（实际仅在启动时调用一次）。
4. 文件权限：使用进程 umask 默认值，不做特殊设置。
5. 并发：`save` 由锁串行化；入站线程与停机主线程不会写同一个临时文件。

## 4. 边界条件

- `path` 父目录不存在 → `save` 时创建；`load` 时视为文件缺失。
- 临时文件已存在（上次崩溃残留）→ 本次用新的唯一文件名，不覆盖；启动时可选清理同目录 `state.*.tmp` 残留。
- `path` 指向目录 → `load` 返回 False（读取失败按损坏处理），`save` 记 error。
- `{"mirror": true}` 之外多出的键 → 忽略，不 warning。

## 5. 错误处理

不向上抛异常。所有失败以日志表达，返回值或落地结果体现。

## 6. 日志要求

| 事件 | 级别 |
|---|---|
| 文件缺失（视为首次启动） | INFO |
| 文件损坏 / 类型非法 | WARNING |
| 写入失败 | ERROR |
| 加载结果 | DEBUG（`mirror loaded: true`） |
| 清理残留临时文件失败 | WARNING |

## 7. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_load_missing | 文件不存在 | 返回 False，INFO |
| test_load_valid | `{"mirror": true}` | 返回 True |
| test_load_corrupt | 非 JSON 内容 | 返回 False + WARNING |
| test_load_wrong_type | `{"mirror": "yes"}` | 返回 False + WARNING |
| test_load_extra_keys | `{"mirror": true, "x": 1}` | 返回 True，无 WARNING |
| test_save_roundtrip | save(True) 后 load | 返回 True；文件内容为合法 JSON 且仅含 mirror |
| test_save_creates_dirs | 父目录不存在 | 自动创建并写入成功 |
| test_save_atomic_no_tmp_left | save 完成后 | 目录中无 `state.*.tmp` 残留 |
| test_save_failure_no_raise | path 指向只读目录 | 不抛异常，记 ERROR |
| test_save_concurrent | 两个线程同时 save(True/False) | 文件始终是合法 JSON，最终值为其中一个；无临时文件残留 |
| test_save_fsync | mock os.fsync | 调用一次 fsync 后才 replace |
