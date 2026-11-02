# 10 · 模块规约：config.py

## 1. 职责

加载并校验 `config.toml`，产出强类型的配置对象。配置规则见 `02-config.md`，本文件定义模块接口与实现要求。

## 2. 接口签名

```python
class ConfigError(Exception):
    """配置加载或校验失败。message 须列出全部问题，每行一条。"""

@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    path: str
    access_token: str
    max_body_bytes: int

@dataclass(frozen=True)
class AuthConfig:
    main_qq: int

@dataclass(frozen=True)
class OnebotConfig:
    api_base: str
    access_token: str

@dataclass(frozen=True)
class TmuxConfig:
    session: str
    kimico_command: str
    history_limit: int
    capture_lines: int
    poll_ms: int
    stable_rounds: int
    volatile_tail_lines: int
    k0_rebuild_threshold: int

@dataclass(frozen=True)
class MirrorConfig:
    chunk_chars: int
    send_interval_ms: int
    outbound_queue_max: int
    batch_quiet_ms: int
    batch_max_chars: int
    ignore_patterns: tuple[str, ...]
    echo_prefix_patterns: tuple[str, ...]

@dataclass(frozen=True)
class RecoveryConfig:
    backoff_seconds: tuple[int, ...]
    max_attempts: int
    window_seconds: int
    success_reset_seconds: int

@dataclass(frozen=True)
class StateConfig:
    path: str

@dataclass(frozen=True)
class LoggingConfig:
    level: str

@dataclass(frozen=True)
class Config:
    server: ServerConfig
    auth: AuthConfig
    onebot: OnebotConfig
    tmux: TmuxConfig
    mirror: MirrorConfig
    recovery: RecoveryConfig
    state: StateConfig
    logging: LoggingConfig

def load_config(path: str) -> Config:
    """读取并校验配置文件。任何失败抛出 ConfigError。"""
```

## 3. 行为规约

1. 用 `tomllib` 解析文件；文件不存在、读取失败、TOML 语法错误 → `ConfigError`。
2. 逐 section 组装：section 缺失时，若其中所有字段都有默认值则用默认值组装；若含必需字段（`server.access_token`、`auth.main_qq`、`onebot.access_token`）→ `ConfigError`。
3. 字段缺失 → 有默认取默认，无默认记为错误。
4. 类型严格匹配（bool 不是 int；`port` 为 str 视为错误）；类型错误记为错误。
5. 取值范围按 `02-config.md` 第 3 节逐字段校验；`mirror.ignore_patterns` 与 `mirror.echo_prefix_patterns` 逐项用 `re.compile` 预编译验证；`logging.level` 归一化为大写存储。
6. 额外校验：
   - `server.access_token` / `onebot.access_token` 不得为 `change-me`、空串或纯空白；
   - `mirror.batch_max_chars >= mirror.chunk_chars`；
   - `tmux.volatile_tail_lines` 必须小于 `tmux.capture_lines`（避免整屏被 mask）。
7. 收集**全部**错误后一次性抛出 `ConfigError`，消息逐行列出（格式：`字段路径: 问题`，如 `server.port: must be int`）。
8. 未知键（含未知 section 与 section 内未知字段）：对每个未知键记 warning 日志（logger 名 `kimico_gateway.config`），不影响加载。
9. 返回的 `Config` 及其子对象不可变（frozen dataclass）；list 字段转为 tuple，避免深层可变。

## 4. 边界条件

- 空文件：所有 section 缺失 → 因 3 个必需字段缺失而抛 `ConfigError`。
- `backoff_seconds = []` → 错误（非空约束）；`backoff_seconds = [0]` 合法。
- `path = "cq"`（无 `/` 前缀）→ 错误。
- `api_base` 末尾多余 `/`：实现时去除尾部 `/` 后存储，避免拼接出 `//send_private_msg`。
- `main_qq` 写成字符串 `"1000000000"` → 类型错误（TOML 中 int 与 str 有区分，不自动转换）。
- `logging.level = "info"` 合法，存储为 `"INFO"`。
- `access_token = "change-me"` → `ConfigError`，消息含 `server.access_token`。
- `batch_max_chars < chunk_chars` → `ConfigError`，消息含两个字段路径。

## 5. 错误处理

- 所有失败统一为 `ConfigError`，不抛其他异常向上。
- 本模块不调用 `sys.exit`；退出码与打印由 `__main__` 负责（见 `18-mod-main.md`）。

## 6. 日志要求

| 事件 | 级别 | 内容 |
|---|---|---|
| 未知键 | WARNING | `unknown config key: server.foo` |
| 加载成功 | DEBUG | 配置摘要，**token 必须打码**（如 `chan***ge-me` 或 `***`） |
| 非回环 host | WARNING | `server.host is not loopback: ...` |

## 7. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_load_minimal | 仅 3 个必需字段，其余缺省 | 各字段等于 `02-config.md` 默认值 |
| test_load_full | 全字段自定义值 | 逐字段等于自定义值 |
| test_missing_required | 缺 `auth.main_qq` | 抛 ConfigError，消息含 `auth.main_qq` |
| test_type_error | `port = "8080"` | 抛 ConfigError，消息含 `server.port` |
| test_range_error | `port = 70000`、`poll_ms = 10` | 抛 ConfigError，两个错误都在消息中 |
| test_bad_regex | `ignore_patterns = ["["]` / `echo_prefix_patterns = ["["]` | 抛 ConfigError |
| test_default_token_rejected | `access_token = "change-me"` | 抛 ConfigError |
| test_batch_lt_chunk | `batch_max_chars < chunk_chars` | 抛 ConfigError |
| test_unknown_key_warn | 额外未知键与未知 section | 正常加载且产生 WARNING 日志 |
| test_empty_file | 空文件 | 抛 ConfigError，列出全部必需字段 |
| test_trailing_slash_api_base | `api_base = "http://x/"` | 存储为 `"http://x"` |
| test_level_case | `level = "debug"` | 存储为 `"DEBUG"` |
| test_frozen_deep | 修改返回的 `ignore_patterns` | 抛 AttributeError/TypeError；字段为 tuple |
