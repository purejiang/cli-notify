# Phase 3: 中继端 v4 开发详细设计

> 依赖：Phase 1 Protocol v2 完成
> 核心原则：**中继是盲管道** — 只读插件放的 routing 元数据做转发/配对，Hook 数据完全不碰

## 1. 中继职责边界

```
Envelope = 插件添加的 routing 元数据 + Hook 原始数据(data.raw)
                ↑                              ↑
          中继读这里做路由              中继完全不碰（可能加密了）
```

| 中继要读的（routing 用） | 中继不碰的 |
|------------------------|-----------|
| `from` — 消息方向 | `data.raw` — Hook 原始 JSON |
| `msgType` — event(直转) / request(等响应) / response(匹配) | `data.tool_use_id`、`data.agent_id`、`data.turn_id`、`data.truncated` |
| `correlationId` — 配对 request ↔ response | `data` 内所有业务字段 |
| `type` — 只判断 3 种审批类型 | 加密后的 `{ephemeralKey, iv, ciphertext}` |

**中继本质上是一个带 JWT 认证 + 审批配对 + 离线排队的消息管道。**

## 2. 审批决策传递方式

mobile 发审批响应时，决策放在 envelope **顶层** `_decision` 字段（下划线 = 中继元数据，不算业务数据）：

```json
{
  "type": "permission_request",
  "msgType": "response",
  "from": "mobile",
  "correlationId": "abc",
  "encrypted": true,
  "data": { "ephemeralKey": "...", "iv": "...", "ciphertext": "..." },
  "_decision": { "decision": "allow", "reason": "Looks safe" }
}
```

## 3. 模型: `app/models.py`

```python
"""中继只用到的类型 — 极简"""
from typing import Literal, Optional, Any
from dataclasses import dataclass, field

APPROVAL_TYPES = {"pre_tool_use", "permission_request", "elicitation"}

ErrorCode = Literal[
    "INVALID_ENVELOPE", "AUTH_FAILED", "ROOM_NOT_FOUND",
    "REQUEST_TIMEOUT", "RATE_LIMITED", "INTERNAL",
    "CONNECTION_LIMIT",
]

@dataclass
class Room:
    desktop: Optional[Any] = None
    mobile: set = field(default_factory=set)
```

## 4. `/hook/relay` — 验证 envelope 包装，不验证 data 内容

```python
@router.post("/hook/relay")
async def hook_relay(request: Request, token: str = Query(...)):

    # 1. JWT 验证
    user_id = verify_token(token)
    if not user_id:
        return auth_failed("Invalid token")

    # 2. 解析 JSON
    try:
        body = await request.json()
    except Exception:
        return invalid_envelope("Not valid JSON")

    # 3. envelope 顶层字段验证（包装层必须完整）
    required_top = ["type", "id", "msgType", "sessionId", "from", "timestamp", "encrypted", "data"]
    for field in required_top:
        if field not in body:
            return invalid_envelope(f"Missing required field: {field}")

    if body["msgType"] not in ("event", "request", "response"):
        return invalid_envelope(f"Invalid msgType: {body['msgType']}")

    if body["from"] not in ("desktop", "mobile", "server"):
        return invalid_envelope(f"Invalid from: {body['from']}")

    if not isinstance(body["timestamp"], (int, float)) or body["timestamp"] < 0:
        return invalid_envelope("Invalid timestamp")

    if not isinstance(body["encrypted"], bool):
        return invalid_envelope("encrypted must be boolean")

    if not isinstance(body["data"], dict):
        return invalid_envelope("data must be an object")

    # data 内部不验证！加密时只需确认 EncryptedPayload 格式
    if body["encrypted"]:
        data_keys = body["data"].keys()
        if not {"ephemeralKey", "iv", "ciphertext"}.issubset(data_keys):
            return invalid_envelope("Encrypted data missing ephemeralKey/iv/ciphertext")
    # 明文时不检查 data.raw/data.truncated 等 —— 中继不管内容

    if body["msgType"] in ("request", "response") and not body.get("correlationId"):
        return invalid_envelope("msgType=request/response requires correlationId")

    # 4. 路由
    try:
        result = await hub.route_message(user_id, body)
    except Exception as e:
        return internal(str(e))

    return result or {"status": "ok"}
```

**验证的**：type/id/msgType/sessionId/from/timestamp/encrypted/data-type —— envelope 包装层
**不验证的**：data.raw、data.truncated、data.tool_use_id 等 —— 内容层，中继不关心

## 5. 消息路由: `app/hub.py`

```python
class Hub:
    def __init__(self):
        self._rooms: dict[str, Room] = {}
        self._queues: dict[Any, asyncio.Queue] = {}
        self._approval_mgr = ApprovalManager()

    async def route_message(self, user_id: str, envelope: dict) -> Optional[dict]:
        """只看 from + msgType + type(审批判断)"""

        from_peer = envelope.get("from")
        msg_type = envelope.get("msgType")
        event_type = envelope.get("type", "")

        if from_peer == "desktop":
            if msg_type == "request" and event_type in APPROVAL_TYPES:
                # 审批 → 广播 mobile + 创建 Future
                await self.broadcast_to_mobiles(user_id, envelope)
                prefs = await self.get_user_preferences(user_id)
                return await self._approval_mgr.create(
                    envelope["correlationId"], user_id, self,
                    timeout_ms=prefs.get("approval_timeout_ms", 30000),
                    fallback_action=prefs.get("fallback_action", "ask"),
                )

            elif msg_type == "request":
                # 其他 request（sync/set_preferences）
                return await self._handle_system_request(user_id, envelope)

            else:
                # event → 广播 mobile
                await self.broadcast_to_mobiles(user_id, envelope)

        elif from_peer == "mobile":
            if msg_type == "response" and event_type in APPROVAL_TYPES:
                # 审批响应 → 解析 Future
                self._approval_mgr.resolve(envelope["correlationId"], envelope)

            elif msg_type == "request":
                return await self._handle_system_request(user_id, envelope)

            else:
                # mobile → desktop 转发
                await self._send_to_desktop(user_id, envelope)

        elif from_peer == "server":
            await self.broadcast_to_user(user_id, envelope)

        return None


class ApprovalManager:
    """审批 Future — 只通过 correlationId + _decision 操作"""

    def __init__(self):
        self._futures: dict[str, asyncio.Future] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._user_ids: dict[str, str] = {}

    async def create(self, correlation_id, user_id, hub,
                     timeout_ms=30000, fallback_action="ask") -> dict:
        future = asyncio.get_event_loop().create_future()
        self._futures[correlation_id] = future
        self._user_ids[correlation_id] = user_id

        timer = asyncio.create_task(
            self._monitor(correlation_id, user_id, hub,
                          timeout_ms / 1000.0, fallback_action)
        )
        self._timers[correlation_id] = timer

        try:
            return await future
        except asyncio.CancelledError:
            return {"decision": fallback_action}

    async def _monitor(self, corr_id, user_id, hub, timeout_s, fallback):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            room = hub._rooms.get(user_id)
            if room and not room.mobile:  # 所有 mobile 离线 → 立即 fallback
                self._resolve_if_pending(corr_id, fallback)
                return
        self._resolve_if_pending(corr_id, fallback)

    def resolve(self, correlation_id: str, envelope: dict):
        """从 envelope 顶层 _decision 提取决策"""
        future = self._futures.pop(correlation_id, None)
        timer = self._timers.pop(correlation_id, None)
        self._user_ids.pop(correlation_id, None)
        if timer:
            timer.cancel()

        if future and not future.done():
            dec = envelope.get("_decision", {})
            if isinstance(dec, dict):
                future.set_result({
                    "decision": dec.get("decision", "deny"),
                    "reason": dec.get("reason", ""),
                })
            else:
                future.set_result({"decision": "deny", "reason": "Invalid"})

    def _resolve_if_pending(self, corr_id, fallback):
        future = self._futures.get(corr_id)
        if future and not future.done():
            future.set_result({"decision": fallback})

    def cancel(self, correlation_id: str):
        future = self._futures.pop(correlation_id, None)
        timer = self._timers.pop(correlation_id, None)
        self._user_ids.pop(correlation_id, None)
        if timer: timer.cancel()
        if future and not future.done(): future.cancel()
```

## 6. WebSocket 连接管理

```python
MAX_MOBILE_CONNECTIONS = 5

async def accept_and_join(self, user_id: str, ws: WebSocket, role: str):
    room = self._rooms.get(user_id) or Room()
    self._rooms[user_id] = room

    if role == "desktop":
        # 新 desktop 顶替旧连接
        if room.desktop is not None:
            try: await room.desktop.close(code=4001, reason="replaced")
            except: pass
            self._cleanup_connection(room.desktop, user_id)
        room.desktop = ws

    elif role == "mobile":
        if len(room.mobile) >= MAX_MOBILE_CONNECTIONS:
            await ws.close(code=4002, reason="too many connections")
            return
        room.mobile.add(ws)

    self._queues[ws] = asyncio.Queue(maxsize=256)
    asyncio.create_task(self._sender_loop(ws))

    await self._send_json(ws, {
        "type": "auth_success", "msgType": "event", "from": "server",
        "data": {"raw": {"user_id": user_id, "role": role}},
    })

    if role == "mobile":
        await self._deliver_queue(user_id)
        await self._sync_preferences(user_id, ws)


async def leave(self, user_id: str, ws: WebSocket):
    room = self._rooms.get(user_id)
    if room is None: return
    if ws == room.desktop:
        room.desktop = None
        for corr_id, uid in list(self._approval_mgr._user_ids.items()):
            if uid == user_id:
                self._approval_mgr.cancel(corr_id)
    elif ws in room.mobile:
        room.mobile.discard(ws)
    self._cleanup_connection(ws, user_id)
```

## 7. 数据库变更

```sql
ALTER TABLE user_preferences ADD COLUMN fallback_action TEXT DEFAULT 'ask';
```

## 8. 产出物清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/models.py` | 精简 | 删除 Envelope/EnvelopeData/EventType，只留 Room + ErrorCode + APPROVAL_TYPES |
| `app/routers.py` | 精简 | `/hook/relay` 只查 from/msgType/correlationId |
| `app/hub.py` | 更新 | ApprovalManager 从 `_decision` 读决策；连接限制；路由只看 routing 元数据 |
| `app/database.py` | 更新 | preferences 加 fallback_action |
| `app/config.py` | 更新 | MAX_MOBILE_CONNECTIONS |
| `app/auth.py` | 不变 | JWT 机制保持 |
