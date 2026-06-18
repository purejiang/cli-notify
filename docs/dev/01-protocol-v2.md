# Phase 1: Protocol v2 开发详细设计

> 依赖：无（最底层，先完成）
> 产出：`protocol/schema.json` + test fixtures + schema validation tests

## 1. 设计原则

- EventType = Hook 名 snake_case（如 `SessionStart` → `session_start`），直接对应
- data 内含 `raw`（Hook 原始 JSON 全量透传）+ 可选元数据字段
- 未知 Hook 不建新 EventType，统一用 `unknown_hook` + raw 透传
- 审批/响应用 `msgType: request/response` + `correlationId` 配对

## 2. Envelope v2 结构

```json
{
  "type": "session_start",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "msgType": "event",
  "sessionId": "abc123",
  "from": "desktop",
  "timestamp": 1718700000000,
  "encrypted": false,
  "data": {
    "raw": { /* Hook stdin JSON 全量 */ },
    "tool_use_id": null,
    "agent_id": null,
    "turn_id": null,
    "truncated": false
  },
  "correlationId": null,
  "groupId": null,
  "_decision": null
}
```

> `_decision` 是**中继层元数据字段**（下划线前缀约定），仅用于 mobile→relay 审批响应。不经过 E2EE 加密，中继直接读取。其他场景下为 null。

### 必填字段 (8)
| 字段 | 类型 | 约束 |
|------|------|------|
| `type` | string | EventType 枚举之一 |
| `id` | string | UUID v4 |
| `msgType` | string | `"event"` \| `"request"` \| `"response"` |
| `sessionId` | string | Claude Code session ID |
| `from` | string | `"desktop"` \| `"mobile"` \| `"server"` |
| `timestamp` | integer | Unix epoch ms, >= 0 |
| `encrypted` | boolean | data 是否加密 |
| `data` | object | 见下 |

### 可选字段 (3)
| 字段 | 类型 | 语义 |
|------|------|------|
| `correlationId` | string\|null | request↔response 配对 key |
| `groupId` | string\|null | Agent 分组 / Turn 分组 key |
| `_decision` | object\|null | 中继层元数据。mobile 审批响应专用 `{"decision":"allow\|deny","reason":""}`，不加密 |

### data 子字段 (5)
| 字段 | 类型 | 语义 |
|------|------|------|
| `raw` | object | **必填**。Hook stdin JSON 全量 |
| `tool_use_id` | string\|null | 工具调用唯一 ID（来自 Hook） |
| `agent_id` | string\|null | Subagent 唯一 ID（来自 Hook） |
| `turn_id` | string\|null | 对话轮次 ID（插件生成） |
| `truncated` | boolean | **必填**。data.raw 是否被截断 |

### msgType 语义
- **event**：单向通知，不等待响应（绝大多数 Hook）
- **request**：需要对方响应（审批请求、Elicitation、sync、preferences）
- **response**：对 request 的应答，`correlationId` 匹配

### from 语义
- **desktop**：消息来自 Claude Code 桌面端（插件发出）
- **mobile**：消息来自 Android 手机
- **server**：消息来自中继（auth_success、error、preferences 同步）

## 3. EventType 完整枚举 (30 + 7 系统类型)

### 3.1 默认启用 (18)

| # | Hook 名 | EventType | msgType | 匹配 key |
|---|---------|-----------|---------|----------|
| 1 | SessionStart | `session_start` | event | — |
| 2 | SessionEnd | `session_end` | event | — |
| 3 | UserPromptSubmit | `user_prompt_submit` | event | — |
| 4 | PreToolUse | `pre_tool_use` | event/request* | tool_use_id |
| 5 | PostToolUse | `post_tool_use` | event | tool_use_id |
| 6 | PostToolUseFailure | `post_tool_use_failure` | event | tool_use_id |
| 7 | PostToolBatch | `post_tool_batch` | event | — |
| 8 | PermissionRequest | `permission_request` | request† | tool_use_id |
| 9 | PermissionDenied | `permission_denied` | event | tool_use_id |
| 10 | Stop | `stop` | event | — |
| 11 | StopFailure | `stop_failure` | event | — |
| 12 | Notification | `notification` | event | — |
| 13 | MessageDisplay | `message_display` | event | message_id |
| 14 | SubagentStart | `subagent_start` | event | agent_id |
| 15 | SubagentStop | `subagent_stop` | event | agent_id |
| 16 | TaskCreated | `task_created` | event | task_id |
| 17 | TaskCompleted | `task_completed` | event | task_id |
| 18 | Elicitation | `elicitation` | request† | — |

> \* PreToolUse: 默认 event，当 approval_mode 为 app/hybrid 时变为 request
> † PermissionRequest/Elicitation: 始终 request

### 3.2 可选启用 (12)

| # | Hook 名 | EventType | msgType | 匹配 key |
|---|---------|-----------|---------|----------|
| 19 | UserPromptExpansion | `user_prompt_expansion` | event | — |
| 20 | Setup | `setup` | event | — |
| 21 | PreCompact | `pre_compact` | event | — |
| 22 | PostCompact | `post_compact` | event | — |
| 23 | TeammateIdle | `teammate_idle` | event | — |
| 24 | ConfigChange | `config_change` | event | — |
| 25 | CwdChanged | `cwd_changed` | event | — |
| 26 | FileChanged | `file_changed` | event | — |
| 27 | InstructionsLoaded | `instructions_loaded` | event | — |
| 28 | WorktreeCreate | `worktree_create` | event | — |
| 29 | WorktreeRemove | `worktree_remove` | event | — |
| 30 | ElicitationResult | `elicitation_result` | event | — |

### 3.3 系统类型 (7)

| EventType | msgType | 说明 |
|-----------|---------|------|
| `key.exchange` | event/response | E2EE 公钥交换 |
| `set_preferences` | request/response | 更新用户偏好 |
| `get_preferences` | request/response | 获取用户偏好 |
| `preferences` | event | 服务端→客户端同步偏好 |
| `sync` | request/response | 同步请求（sessions/full_message 等） |
| `auth_success` | event | 认证成功通知 |
| `error` | event | 错误通知 |

## 4. 响应类型（msgType=response）

响应**复用相同 EventType**，通过 `msgType: "response"` + 匹配的 `correlationId` 区分。

### 4.1 pre_tool_use 响应 (mobile → desktop)

```json
{
  "type": "pre_tool_use",
  "msgType": "response",
  "from": "mobile",
  "correlationId": "<请求的 correlationId>",
  "data": {
    "raw": {},
    "tool_use_id": "<请求的 tool_use_id>",
    "agent_id": null,
    "turn_id": null,
    "truncated": false,
    "decision": "allow",
    "reason": "Looks safe"
  }
}
```

### 4.2 permission_request 响应 (mobile → desktop)

```json
{
  "type": "permission_request",
  "msgType": "response",
  "from": "mobile",
  "correlationId": "<请求的 correlationId>",
  "data": {
    "raw": {},
    "tool_use_id": "<请求的 tool_use_id>",
    "agent_id": null,
    "turn_id": null,
    "truncated": false,
    "decision": "allow",
    "reason": "Approved by user"
  }
}
```

### 4.3 elicitation 响应 (mobile → desktop)

```json
{
  "type": "elicitation",
  "msgType": "response",
  "from": "mobile",
  "correlationId": "<请求的 correlationId>",
  "data": {
    "raw": {},
    "agent_id": null,
    "turn_id": null,
    "truncated": false,
    "action": "accept",
    "content": { "username": "alice" }
  }
}
```

## 5. 系统类型详细定义

### 5.1 key.exchange

**请求 (mobile → server):**
```json
{
  "type": "key.exchange",
  "msgType": "event",
  "from": "mobile",
  "data": {
    "raw": {
      "publicKey": "base64(65-byte P-256 uncompressed)",
      "algorithm": "ECDH-P256-AES256-GCM"
    },
    "truncated": false
  }
}
```

**响应 (server → mobile):**
```json
{
  "type": "key.exchange",
  "msgType": "response",
  "from": "server",
  "correlationId": "<请求 id>",
  "data": {
    "raw": {
      "status": "ok",
      "fingerprint": "sha256 hex"
    },
    "truncated": false
  }
}
```

### 5.2 set_preferences

**请求 (mobile → server):**
```json
{
  "type": "set_preferences",
  "msgType": "request",
  "from": "mobile",
  "correlationId": "uuid",
  "data": {
    "raw": {
      "approval_timeout_ms": 60000,
      "fallback_action": "allow"
    },
    "truncated": false
  }
}
```

### 5.3 sync

**请求 (mobile → server):**
```json
{
  "type": "sync",
  "msgType": "request",
  "from": "mobile",
  "correlationId": "uuid",
  "data": {
    "raw": {
      "query": "sessions",
      "params": {}
    },
    "truncated": false
  }
}
```
支持的 query: `"sessions"` | `"full_message"` | `"preferences"`

### 5.4 error

```json
{
  "type": "error",
  "msgType": "event",
  "from": "server",
  "data": {
    "raw": {
      "code": "REQUEST_TIMEOUT",
      "message": "Approval request timed out",
      "detail": {}
    },
    "truncated": false
  }
}
```

## 6. ErrorCode 枚举

```json
[
  "INVALID_ENVELOPE",
  "UNKNOWN_TYPE",
  "AUTH_FAILED",
  "ROOM_NOT_FOUND",
  "REQUEST_TIMEOUT",
  "RATE_LIMITED",
  "INTERNAL",
  "CONNECTION_LIMIT",
  "ENCRYPTION_FAILED"
]
```

新增：`CONNECTION_LIMIT`（连接数超限）、`ENCRYPTION_FAILED`（加解密失败）

## 7. EncryptedPayload (不变)

```json
{
  "ephemeralKey": "base64(65-byte uncompressed P-256 public key)",
  "iv": "base64(12-byte AES-GCM IV)",
  "ciphertext": "base64(ciphertext + 16-byte auth tag)"
}
```

当 `encrypted: true` 时，`data` 字段为 EncryptedPayload 结构。

## 8. 完整 JSON Schema 定义

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://cli-notify.dev/protocol/v2",
  "title": "CLI-Notify Protocol v2",
  "description": "Message bus protocol v2. 30 Claude Code Hooks → EventTypes.",
  "type": "object",
  "required": ["type", "id", "msgType", "sessionId", "from", "timestamp", "encrypted", "data"],
  "properties": {
    "type": { "$ref": "#/$defs/EventType" },
    "id": { "type": "string", "format": "uuid" },
    "msgType": { "$ref": "#/$defs/MsgType" },
    "correlationId": { "type": ["string", "null"], "format": "uuid" },
    "groupId": { "type": ["string", "null"] },
    "sessionId": { "type": "string" },
    "from": { "$ref": "#/$defs/Peer" },
    "timestamp": { "type": "integer", "minimum": 0 },
    "encrypted": { "type": "boolean" },
    "data": {
      "oneOf": [
        { "$ref": "#/$defs/EnvelopeData" },
        { "$ref": "#/$defs/EncryptedPayload" }
      ]
    }
  },
  "$defs": {
    "MsgType": {
      "type": "string",
      "enum": ["event", "request", "response"]
    },
    "Peer": {
      "type": "string",
      "enum": ["desktop", "mobile", "server"]
    },
    "EventType": {
      "type": "string",
      "enum": [
        "session_start", "session_end",
        "user_prompt_submit",
        "pre_tool_use", "post_tool_use", "post_tool_use_failure", "post_tool_batch",
        "permission_request", "permission_denied",
        "stop", "stop_failure",
        "notification", "message_display",
        "subagent_start", "subagent_stop",
        "task_created", "task_completed",
        "elicitation", "elicitation_result",
        "user_prompt_expansion", "setup",
        "pre_compact", "post_compact",
        "teammate_idle",
        "config_change", "cwd_changed", "file_changed", "instructions_loaded",
        "worktree_create", "worktree_remove",
        "key.exchange", "set_preferences", "get_preferences", "preferences",
        "sync", "auth_success", "error"
      ]
    },
    "ErrorCode": {
      "type": "string",
      "enum": [
        "INVALID_ENVELOPE", "UNKNOWN_TYPE", "AUTH_FAILED", "ROOM_NOT_FOUND",
        "REQUEST_TIMEOUT", "RATE_LIMITED", "INTERNAL",
        "CONNECTION_LIMIT", "ENCRYPTION_FAILED"
      ]
    },
    "EnvelopeData": {
      "type": "object",
      "required": ["raw", "truncated"],
      "properties": {
        "raw": { "type": "object" },
        "tool_use_id": { "type": ["string", "null"] },
        "agent_id": { "type": ["string", "null"] },
        "turn_id": { "type": ["string", "null"] },
        "truncated": { "type": "boolean" }
      }
    },
    "EncryptedPayload": {
      "type": "object",
      "required": ["ephemeralKey", "iv", "ciphertext"],
      "properties": {
        "ephemeralKey": { "type": "string" },
        "iv": { "type": "string" },
        "ciphertext": { "type": "string" }
      }
    }
  }
}
```

## 9. 测试 Fixtures 计划

### valid-envelopes.json (约 37 条)
- 18 核心 EventType × 1 = 18 条
- 12 扩展 EventType × 1 = 12 条
- 系统类型 × 3（key.exchange, set_preferences, sync）= 3 条
- encrypted envelope × 1
- request/response 配对 × 3（pre_tool_use, permission_request, elicitation）= 3 条

### invalid-envelopes.json (约 15 条)
- 缺少必填字段 × 8（type, id, msgType, sessionId, from, timestamp, encrypted, data）
- 错误枚举值 × 3（错误 EventType, 错误 msgType, 错误 from）
- 错误类型 × 2（data 不是 object, encrypted 不是 boolean）
- 负数/零 timestamp × 1
- data 缺少 raw 字段 × 1

### schema-validation.test.js 新增测试
- EnvelopeData 必须含 truncated (boolean)
- correlationId 格式验证
- 30 种 EventType 全覆盖
- encrypted=true 时 data 必须匹配 EncryptedPayload
- tool_use_id/agent_id/turn_id 可为 null

## 10. 产出物清单

| 文件 | 操作 |
|------|------|
| `protocol/schema.json` | 完全重写 |
| `tests/fixtures/valid-envelopes.json` | 完全重写（约 37 条） |
| `tests/fixtures/invalid-envelopes.json` | 更新（约 15 条） |
| `tests/protocol/schema-validation.test.js` | 更新 |
