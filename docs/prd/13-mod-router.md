# 13 · 模块规约：router.py

## 1. 职责

- 解析控制指令（`#` 开头）：纯函数，无副作用。
- 生成全部固定文案（回执、提示、告警、#status 文本）。
- 控制指令的**副作用**（开关 mirror、清输出 lane、递增 generation、重置基线）由 input worker 调用其他模块执行（见 `14-mod-input.md`），router 只负责判定与文案。

## 2. 固定文案表（唯一来源，逐字使用）

| 常量名 | 文本 | 触发 |
|---|---|---|
| `MIRROR_ON_REPLY` | `mirror 已开启` | `#mirror on` |
| `MIRROR_OFF_REPLY` | `mirror 已关闭，待发送输出已清空` | `#mirror off` |
| `CLEAR_REPLY` | `输出缓存已清空，基线已重置（不影响 kimico 上下文）` | `#clear` |
| `USAGE_TEXT` | `未知指令。可用：#mirror on \| #mirror off \| #status \| #clear` | 未知 # 指令 |
| `NOT_TEXT_REPLY` | `仅支持文本消息` | 消息无有效文本段 |
| `BUSY_REPLY` | `当前繁忙，请稍后重试` | 入站队列满 |
| `OVERFLOW_NOTICE` | `输出过快，部分内容未推送` | 输出 lane 溢出 |
| `INJECT_FAIL_REPLY` | `注入失败，请稍后重试` | tmux 注入失败 |
| `STATUS_FAIL_REPLY` | `状态获取失败，请查看网关日志` | #status 快照获取异常时的降级回执 |
| `RESTART_NOTICE_TEMPLATE` | `kimico 已退出，将在 {wait}s 后尝试重启（第 {attempt}/{max} 次）` | 每次恢复尝试 |
| `ALARM_TEXT_TEMPLATE` | `kimico 在 {window}s 内累计恢复失败 {max} 次，已停止自动重启，请登录机器处理` | 窗口内失败达上限 |

注意：`USAGE_TEXT` 中的竖线两侧各一个空格，整条文案为单行。

## 3. 接口签名

```python
from enum import Enum, auto

class CommandKind(Enum):
    MIRROR_ON = auto()
    MIRROR_OFF = auto()
    STATUS = auto()
    CLEAR = auto()
    UNKNOWN = auto()

def parse_command(text: str) -> CommandKind | None:
    """识别控制指令。返回 None 表示非控制指令（透传给 kimico）。
    规则：text.strip() 后以 '#' 开头才进入指令判定；
    指令名与参数按空白切分，不区分大小写。
    注意：'##' 由 input worker 在调用本函数前处理为字面文本，不进入本函数。"""

@dataclass(frozen=True)
class StatusSnapshot:
    tmux_alive: bool        # tmux 会话是否存在
    kimico_alive: bool      # pane 是否存活（非 dead）
    mirror_on: bool
    inbound_len: int
    inbound_max: int        # 常量 50
    control_len: int        # 控制 lane 当前长度
    control_max: int        # 常量 100（仅展示用软上限）
    bulk_len: int           # 输出 lane 当前长度
    bulk_max: int           # config mirror.outbound_queue_max
    last_output_ts: float | None   # time.time() 墙钟；None 表示尚无输出
    started_monotonic: float       # 网关启动时刻 time.monotonic()

def format_status(snap: StatusSnapshot, now_monotonic: float, now_wall: float) -> str:
    """生成 #status 回执文本（格式见第 4 节）。"""

def render_template(template: str, **kwargs) -> str:
    """对 RESTART_NOTICE_TEMPLATE / ALARM_TEXT_TEMPLATE 做 str.format 渲染。"""
```

## 4. #status 输出模板

逐行输出，字段顺序固定（时间为本地时区），共 8 行：

```
tmux 会话：存活
kimico 进程：运行中
mirror：on
入站队列：0/50
控制队列：0/100
输出队列：3/500
最后输出：2026-11-02 14:33:01
网关运行时长：0h05m12s
```

取值规则：

| 行 | 规则 |
|---|---|
| tmux 会话 | `snap.tmux_alive` → `存活` / `不存在` |
| kimico 进程 | `snap.kimico_alive` → `运行中` / `已退出`（会话不存在时必为 `已退出`） |
| mirror | `on` / `off` |
| 入站队列 | `{inbound_len}/{inbound_max}` |
| 控制队列 | `{control_len}/{control_max}` |
| 输出队列 | `{bulk_len}/{bulk_max}` |
| 最后输出 | `last_output_ts` 格式化为 `YYYY-MM-DD HH:MM:SS`（本地时间）；`None` → `无` |
| 网关运行时长 | `now_monotonic - started_monotonic`，格式 `{h}h{m:02d}m{s:02d}s`（h 不补零，如 `3h12m05s`；不足 1 小时如 `0h05m12s`） |

`now_wall` 仅用于测试注入或将来扩展；当前格式化 `last_output_ts` 直接使用 `time.localtime(last_output_ts)`。

## 5. parse_command 判定表

| 输入（strip 后） | 结果 |
|---|---|
| `#mirror on`、`#MIRROR ON`、`#mirror  on`（多空格） | MIRROR_ON |
| `#mirror off` | MIRROR_OFF |
| `#status` | STATUS |
| `#clear` | CLEAR |
| `#mirror`（缺参数）、`#mirror onn`、`#foo`、`#`、`# status` | UNKNOWN |
| `/session list`、`hello`、`  你好  ` 等非 `#` 开头文本 | None（透传） |
| `#note 讨论`、`#标签文字`（`#` 开头的自然语言） | UNKNOWN（规则统一：凡 `#` 开头不识别的都是未知指令） |

- 判定前 `text.strip()`；指令内部按任意长度空白切分 token。
- token 比较一律 `lower()` 后进行；比较对象是字面量 `#mirror` / `#status` / `#clear` 与 `on` / `off`。
- `#mirror on` 恰好两个 token；多出的 token（如 `#mirror on now`）→ UNKNOWN。
- `##` 不进入本函数；input worker 先把它转为字面文本。

## 6. 边界条件

- 输入为纯空白 → 上游（input worker）已按“空消息”处理，本函数收到的输入保证非空。
- 大小写混合 `#Mirror ON` → MIRROR_ON。
- 全角字符不做归一（如 `＃mirror on` 全角井号 → 不是指令 → 透传注入）。
- `#clear` 的回执在 generation 递增、输出 lane 清空后放入控制 lane；基线由监控线程下一轮重置，旧 generation 段不会发送。

## 7. 错误处理

纯函数模块，无异常路径；不抛自定义异常。

## 8. 日志要求

本模块不记日志（调用方 input worker 对控制指令执行记 INFO）。

## 9. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_parse_mirror_on | 大小写、空格变体 | MIRROR_ON |
| test_parse_mirror_off | 同上 | MIRROR_OFF |
| test_parse_status_clear | `#status` / `#CLEAR` | STATUS / CLEAR |
| test_parse_unknown | `#mirror`、`#mirror on now`、`#`、`#foo` | UNKNOWN |
| test_parse_passthrough | `/session list`、普通中文、`＃mirror on` | None |
| test_parse_double_hash | `##hello` | 直接调用 parse_command 返回 UNKNOWN；input 层在调用前已按 `##` 字面文本处理 |
| test_status_format_full | 全字段快照 | 与第 4 节模板逐字节一致（时间用固定值注入）；共 8 行 |
| test_status_format_no_output | last_output_ts=None | 该行为 `最后输出：无` |
| test_status_uptime_format | 3725 秒 | `1h02m05s` |
| test_templates | 渲染 RESTART/ALARM | 占位符全部替换，数字正确；ALARM 含窗口秒数 |
