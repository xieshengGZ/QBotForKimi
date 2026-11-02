# 12 · 模块规约：onebot.py

## 1. 职责

- HTTP 接入：运行 `ThreadingHTTPServer`，接收 NapCat 的 OneBot v11 事件上报，完成鉴权、校验、去重、文本提取，把合法私聊事件交给上层回调。
- HTTP 出站：提供 `send_private_msg`，调用 NapCat HTTP API 发送私聊文本。

## 2. 内嵌协议：OneBot v11 最小子集

### 2.1 事件上报（NapCat → 网关）

NapCat 以 HTTP POST 把事件发到配置的上报地址（默认 `http://127.0.0.1:8080/cq`）。私聊消息事件示例：

```json
{
  "time": 1762000000,
  "self_id": 2222222222,
  "post_type": "message",
  "message_type": "private",
  "sub_type": "friend",
  "message_id": 12345678,
  "user_id": 1000000000,
  "message": [
    { "type": "text", "data": { "text": "你好" } },
    { "type": "text", "data": { "text": "，继续" } }
  ],
  "raw_message": "你好，继续",
  "font": 0,
  "sender": { "user_id": 1000000000, "nickname": "某某" }
}
```

网关只消费以下字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| post_type | str | 只接受 `"message"`，其他（如 `"notice"`、`"request"`、`"meta_event"`）静默丢弃 |
| message_type | str | 只接受 `"private"`，群消息（`"group"`）静默丢弃 |
| message_id | int | 去重键 |
| user_id | int | 只放行等于 `auth.main_qq` 的事件 |
| message | list | 消息段数组（NapCat 配置为 array 格式，见 `30-deploy.md`） |

消息段结构：`{"type": "text", "data": {"text": "..."}}`；非 text 段（image、face、at、record 等）的 `type` 不同。网关只拼接 text 段。

若 NapCat 配置了鉴权，请求会带头部 `Authorization: Bearer <access_token>`。

### 2.2 发送私聊消息（网关 → NapCat）

请求：

```
POST {api_base}/send_private_msg
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "user_id": 1000000000,
  "message": "要发送的文本",
  "auto_escape": true
}
```

`auto_escape: true` 为**必需**：kimico 输出中可能含有 CQ 码样式的方括号文本，必须按纯文本发送，禁止 NapCat 解析。

成功响应：

```json
{ "status": "ok", "retcode": 0, "data": { "message_id": 12345679 }, "message": "", "wording": "" }
```

判定成功：HTTP 状态码 200 **且** 响应 JSON 中 `retcode == 0`。其他任何情况（非 200、JSON 解析失败、retcode ≠ 0、网络/超时异常）均为发送失败。

### 2.3 事件上报响应约定

网关对上报请求的响应：

- 空 body；NapCat 不要求响应体内容（不支持快速操作）。
- 状态码按下表；**被静默丢弃的事件同样返回 200**，避免 NapCat 判定上报失败而重试。

## 3. 接口签名

```python
class OnebotAPIError(Exception):
    """send_private_msg 失败。"""

@dataclass(frozen=True)
class PrivateMessageEvent:
    message_id: int
    user_id: int
    text: str          # 所有 text 段无分隔拼接的结果
    has_text: bool     # message 数组中是否存在至少一个 text 段

class DedupTable:
    """message_id 去重表，FIFO，容量 200。线程安全。"""
    def __init__(self, capacity: int = 200) -> None: ...
    def seen_or_add(self, message_id: int) -> bool:
        """已存在返回 True（重复）；否则插入并返回 False。满容量时淘汰最旧条目。"""

def extract_text(message: list) -> tuple[str, bool]:
    """拼接 message 数组中所有 type=="text" 段的 data.text（无分隔符）。
    返回 (text, has_text)。非 list 输入按 (\"\", False) 处理。"""

# HTTP 接入
EventCallback = Callable[[PrivateMessageEvent], None]

class OnebotServer:
    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        access_token: str,
        max_body_bytes: int,
        main_qq: int,
        dedup: DedupTable,
        on_event: EventCallback,
    ) -> None: ...
    def serve_forever(self) -> None:
        """阻塞运行（在专属线程中调用）。"""
    def shutdown(self) -> None:
        """线程安全的停止。"""

# HTTP 出站
def send_private_msg(
    api_base: str,
    access_token: str,
    user_id: int,
    text: str,
    timeout: float = 3.0,
) -> None:
    """成功静默返回；失败抛 OnebotAPIError（消息含原因与响应摘要）。"""
```

## 4. 行为规约（HTTP 接入）

请求处理按以下顺序短路执行，每步失败即返回对应状态码并结束：

| 顺序 | 检查 | 失败响应 |
|---|---|---|
| 1 | 方法必须为 POST | 405 |
| 2 | 路径必须等于 `server.path`（精确匹配） | 404 |
| 3 | `Content-Length` 缺失、非数字、或 > `max_body_bytes` | 411（缺失）/ 400（非法）/ 413（超限） |
| 4 | `Authorization` 头必须等于 `Bearer {server.access_token}` | 401 |
| 5 | body 必须是合法 JSON object | 400 |
| 6 | `post_type == "message"` ∧ `message_type == "private"` ∧ `user_id == main_qq` | 不满足 → **200 静默丢弃** |
| 7 | `message_id` 为 int 且经 DedupTable 判定非重复 | 缺失/类型错 → 400；重复 → **200 静默丢弃** |
| 8 | `message` 字段存在（允许空数组） | 缺失 → 400 |
| 9 | `extract_text` 构造 `PrivateMessageEvent`，调用 `on_event(event)` | — |
| 10 | 返回 200 空 body | — |

细则：

- 不校验 `Content-Type`（NapCat 默认 `application/json`，网关不依赖该头）。
- 第 6 步三个条件任一不满足即丢弃，不区分原因、不记 info 以上日志（群消息量大时防刷屏，DEBUG 可记）。
- `on_event` 抛异常：捕获并记 ERROR，仍返回 200（队列满等上层问题不得传导为 5xx，否则 NapCat 会重试造成重复）。
- `sub_type`、`raw_message`、`sender` 等其余字段不读取、不校验。
- HTTP 服务日志：不用 `BaseHTTPRequestHandler` 的默认 stderr 访问日志；重写为 DEBUG 级别 logging。
- 服务使用 `ThreadingHTTPServer`（每请求一个线程），`daemon_threads = True`。

## 5. 行为规约（发送）

1. URL：`api_base.rstrip("/") + "/send_private_msg"`。
2. 请求体按 2.2 节构造（`user_id` int、`message` str、`auto_escape` true），JSON 序列化后 UTF-8 编码。
3. 头部：`Authorization: Bearer {token}`、`Content-Type: application/json`。
4. 用 `urllib.request` 发送，`timeout` 默认 3.0 秒。
5. 成功判定见 2.2 节；失败抛 `OnebotAPIError`，消息包含：失败类别（timeout/http/retcode/parse）与关键摘要（HTTP 码或 retcode 或异常文本）。**不得**在异常消息中包含 token。

## 6. 边界条件

- `message` 为空数组 → `text=""`、`has_text=False`（交给上层按"无文本段"处理）。
- text 段缺 `data` 或 `data.text` → 该段按非 text 段处理（跳过，不影响 has_text 判定……若它是 type=="text" 但无文本，则视为非 text 段）。
- `message_id` 重复到达间隔超过去重表容量（200 条）→ 无法去重，正常入队（已知限制）。
- 极大单行文本：`max_body_bytes` 兜底（413）。
- `send_private_msg` 的 `text` 为任意 Unicode（含换行、emoji、URL），不截断（分段由 mirror 负责）。

## 7. 错误处理

- 接入侧：不向上抛异常；一切问题以状态码 + 日志表达。
- 出站侧：所有失败归一为 `OnebotAPIError`；重试策略由调用方（mirror 发送循环）实现，本函数只尝试一次。

## 8. 日志要求

| 事件 | 级别 | 内容 |
|---|---|---|
| 鉴权失败 | WARNING | 来源 IP（不含 token） |
| JSON 解析失败 / 字段缺失 | WARNING | 摘要 |
| 静默丢弃（非主 QQ / 群消息 / 重复） | DEBUG | 类别与 user_id/message_id |
| 事件入队 | DEBUG | message_id、文本长度 |
| 发送失败 | 由调用方记（本模块不记） | — |

## 9. 测试清单

| 测试 | 场景 | 断言 |
|---|---|---|
| test_extract_text_multi | 两个 text 段夹一个 image 段 | text 拼接无分隔、has_text=True |
| test_extract_text_none | 仅 image 段 | `("", False)` |
| test_extract_text_malformed | text 段无 data | 跳过该段 |
| test_dedup_fifo | 插入 201 个 id | 第 1 个被淘汰可重复插入；中间 id 判重 |
| test_server_happy_path | 合法事件 POST | 200；on_event 收到 event，字段正确 |
| test_server_wrong_path | POST 到 /other | 404，无 event |
| test_server_get | GET /cq | 405 |
| test_server_bad_token | 错误/缺失 Authorization | 401，无 event |
| test_server_oversize | Content-Length 超限 | 413，不读 body |
| test_server_bad_json | 非法 JSON | 400 |
| test_server_group_silent | message_type=group | 200，无 event |
| test_server_other_user_silent | user_id 非主 QQ | 200，无 event |
| test_server_duplicate_silent | 同 message_id 连发两次 | 均 200，event 仅一次 |
| test_server_callback_raise | on_event 抛异常 | 仍 200，记 ERROR |
| test_send_success | 假 HTTP 服务返回 retcode 0 | 正常返回；请求体含 auto_escape=true |
| test_send_retcode_fail | retcode=104 | OnebotAPIError |
| test_send_timeout | 假服务不响应 | OnebotAPIError（类别 timeout） |

（HTTP 服务测试用随机空闲端口启动真实 `OnebotServer`，用 `urllib` 或 `http.client` 发请求。）
