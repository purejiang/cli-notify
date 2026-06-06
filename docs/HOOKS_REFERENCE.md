# Claude Code Hooks 参考文档

## 概览

Claude Code 在生命周期节点触发 HTTP hook，POST JSON 到 relay。Relay 处理后通过 WebSocket 推送到手机。

共 8 个 hook，全部 HTTP 类型，配置在 `cli-notify-plugin/hooks/hooks.json`。

---

## Hook 清单

| Hook | 触发时机 | Relay 路径 | 发送的 WebSocket 事件 |
|------|---------|------------|----------------------|
| **SessionStart** | 会话创建/恢复 | `/hook/session-start` | `session.start` |
| **UserPromptSubmit** | 用户提交消息 | `/hook/user-prompt` | `message.user` |
| **PreToolUse** | 工具调用**前** | `/hook/pre-tool-use` | `tool.request` |
| **PermissionRequest** | 桌面弹权限窗时 | `/hook/permission-request` | `tool.permission_request` |
| **PostToolUse** | 工具调用**后** | `/hook/post-tool-use` | `tool.result` |
| **Stop** | Claude 回复完成 | `/hook/stop` | `message.assistant` + `notification(idle_prompt)` |
| **SessionEnd** | 会话结束 | `/hook/session-end` | `session.end` |
| **Notification** | 系统通知 | `/hook/notification` | `notification` |

---

## 各 Hook 的 Request Body

### SessionStart
```json
{
  "session_id": "abc123...",
  "cwd": "/path/to/project"
}
```

### UserPromptSubmit
```json
{
  "session_id": "abc123...",
  "prompt": "用户输入的文本"
}
```

### PreToolUse
```json
{
  "session_id": "abc123...",
  "tool_name": "Bash",
  "tool_input": {
    "command": "echo hello",
    "description": "Say hello"
  }
}
```

**Relay 返回**（不阻塞，通知手机即可）:
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "Forwarded to mobile for notification only"
  }
}
```

### PermissionRequest
```json
{
  "session_id": "abc123...",
  "tool_name": "Bash",
  "tool_input": { "command": "echo hello" }
}
```

### PostToolUse — `tool_response` 数据结构

`body.tool_response` 是工具的实际返回值，格式因工具而异。

#### Bash
```json
{
  "stdout": "命令输出\n",
  "stderr": "",
  "interrupted": false,
  "isImage": false,
  "noOutputExpected": false
}
```
- 提取字段: `stdout`（有 stderr 时追加）

#### Read
```json
{
  "type": "text",
  "file": {
    "filePath": "/absolute/path/to/file.kt",
    "content": "文件内容..."
  }
}
```
- 提取字段: `file.content`

#### Write
```json
{
  "type": "write",
  "file": {
    "filePath": "/absolute/path/to/file.kt",
    "content": "写入的内容"
  }
}
```
- 提取字段: `file.content`

#### Edit
```json
{
  "filePath": "/path/to/file.kt",
  "oldString": "旧代码...",
  "newString": "新代码...",
  "originalFile": "原始文件内容...",
  "structuredPatch": "...",
  "userModified": false,
  "replaceAll": false
}
```
- 展示方式: DiffView（用 params 中的 oldString/newString 做红绿 diff）

#### Grep
```json
{
  "mode": "content",
  "numFiles": 2,
  "filenames": ["file1.kt", "file2.kt"],
  "content": "file1.kt:42: matched line\nfile2.kt:8: another match\n",
  "numLines": 2
}
```
- 提取字段: `content`

#### Glob
```json
{
  "filenames": [
    "app/src/main/java/com/example/File1.kt",
    "app/src/main/java/com/example/File2.kt"
  ],
  "durationMs": 1,
  "numFiles": 13,
  "truncated": false
}
```
- 提取字段: `filenames` 数组（换行拼接）

### Stop
```json
{
  "session_id": "abc123...",
  "cwd": "/path/to/project",
  "last_assistant_message": "Claude 的回复全文..."
}
```
- relay 提取 `last_assistant_message` 发送给手机
- 同时附带 `cwd`（兜底，以防 SessionStart 错过的场景）

### SessionEnd
```json
{
  "session_id": "abc123...",
  "reason": "completed 或用户关闭等"
}
```

### Notification
```json
{
  "session_id": "abc123...",
  "notification_type": "permission_prompt | idle_prompt | auth_success",
  "message": "通知文本"
}
```

---

## WebSocket 事件格式

Relay 通过 `makeEvent()` 包装后发送。所有事件共用外层结构：

```json
{
  "type": "tool.request",
  "id": "uuid",
  "from": "desktop",
  "sessionId": "abc123...",
  "timestamp": 1717551234567,
  "data": { /* 工具相关的数据 */ }
}
```

### 事件类型一览

| type | 触发 hook | data 字段 |
|------|----------|----------|
| `session.start` | SessionStart | `{ cwd }` |
| `message.user` | UserPromptSubmit | `{ content }` |
| `tool.request` | PreToolUse | `{ toolName, params }` |
| `tool.permission_request` | PermissionRequest | `{ toolName, params }` |
| `tool.result` | PostToolUse | `{ toolName, output, success }` |
| `message.assistant` | Stop | `{ content, model, tokens, stopReason }` |
| `session.end` | SessionEnd | `{ reason }` |
| `notification` | Stop / Notification | `{ kind, message, cwd }` |

---

## Android 端数据流

```
WebSocket 事件
  → EventParser.parse()        → SessionEvent（sealed class）
  → EventBridge.toNotificationMessage() → NotificationMessage
  → NotificationStore.addMessage()
  → UI (DetailScreen / NotificationsPage)
```

### extractOutputContent 提取逻辑

位于 `ToolCallCard.kt`，按优先级：

1. 有 `stdout` 字段 → Bash，提取 stdout+stderr
2. 有 `file` 对象 → Read/Write，提取 `file.content`
3. 有 `content` + `numFiles` → Grep，提取 `content`
4. 有 `filenames` 数组 → Glob，换行拼接
5. 有 `content` 字段 → 通用，提取 content
6. 有 `output` 字段 → 通用，提取 output
7. 都不是 → 原样返回
