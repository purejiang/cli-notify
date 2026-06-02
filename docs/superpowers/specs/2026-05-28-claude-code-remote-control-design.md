# Claude Code Remote Control — Design Spec

**Date:** 2026-05-28
**Status:** Draft (updated with HTTP hooks + plugin architecture)

## Overview

一个允许用户通过 Android 手机实时查看 Claude Code 桌面会话、发送回复消息、审批工具权限的系统。

## System Architecture

```
┌─────────────────────┐  HTTP hooks  ┌──────────────────────┐
│   Claude Code       │ ────────────→ │  Desktop Agent       │
│   + Plugin hooks    │ ←── JSON ──── │  (Node.js, :19527)   │
└─────────────────────┘               │                       │
                                      │  ┌─ HTTP Server       │
                                      │  ├─ Local WS Server   │
                                      │  ├─ Cloud WS Client   │
                                      │  ├─ Permission Mgr    │
                                      │  └─ Session Watcher   │
                                      └──────┬───────┬───────┘
                                             │       │
                                      LAN直连 │       │ Cloud WS
                                             │       │
                                             ▼       ▼
                                      ┌──────────────┐
                                      │  Android App  │
                                      │  (Kotlin +    │
                                      │   Compose)    │
                                      └──────────────┘
                                             │
                                      Cloud WS│ (fallback)
                                             │
                                             ▼
                                      ┌──────────────┐
                                      │  Cloud Relay  │
                                      │  (Python      │
                                      │  FastAPI)     │
                                      │  + FCM Push   │
                                      └──────────────┘
```

## 四个子系统

### 1. Desktop Agent (Node.js + TypeScript)

在桌面后台运行的守护进程，作为 Claude Code HTTP hooks 的接收端和手机客户端的消息枢纽。

**职责：**
- 启动 HTTP + WebSocket 服务（localhost:19527）
- 接收 Claude Code **HTTP hooks** 直接 POST 的 JSON 事件
- 管理 WebSocket 连接（本地直连 + 云端中继双通道）
- 处理 PreToolUse 权限审批（阻塞等待，返回结构化 JSON 决策）
- 监听 sessions 目录变化，解析会话 JSON 文件补全历史
- 执行反向操作（继续会话、审批/拒绝工具）

**Hook 端点（接收 Claude Code 的 HTTP hook POST）：**

| 端点 | Hook 事件 | 行为 |
|---|---|---|
| `POST /hook/session-start` | SessionStart | 创建会话记录，通知手机 |
| `POST /hook/user-prompt` | UserPromptSubmit | 用户提交消息时触发，转发用户输入到手机 |
| `POST /hook/pre-tool-use` | PreToolUse | **阻塞等待**手机审批或超时，返回 `permissionDecision` JSON |
| `POST /hook/post-tool-use` | PostToolUse | 转发工具执行结果到手机 |
| `POST /hook/permission-request` | PermissionRequest | 权限弹窗出现时通知手机（补充提醒） |
| `POST /hook/stop` | Stop | 转发 assistant 回复内容，标记会话进入 idle |
| `POST /hook/session-end` | SessionEnd | 结束会话，通知手机 |
| `POST /hook/notification` | Notification | 通知手机 Claude 需要输入或权限批准 |

**PreToolUse 阻塞审批流程 (核心)：**
```
Claude Code 要调用工具
  → PreToolUse HTTP hook → POST /hook/pre-tool-use
    → Agent 检查白名单 → 命中 → 立即返回 200 {"permissionDecision": "allow"}
    → 未命中 → 推送审批请求到手机
      → 手机回复 approve → 返回 200 {"permissionDecision": "allow"}
      → 手机回复 deny    → 返回 200 {"permissionDecision": "deny"}
      → 60s 超时         → 返回 200 {"permissionDecision": "ask"}  ← 退回桌面弹窗
```

HTTP hook 响应格式（遵循 Claude Code hook 协议）：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "User approved from mobile"
  }
}
```

**Session Watcher：**
- 用 chokidar 监听 `~/.claude/sessions/` 目录
- 解析 session JSON，提取 user/assistant 消息完整文本
- 作为 Hook 事件的补充（Hook 提供实时流，文件提供完整记录和回放）

### 2. Cloud Relay Server (Python + FastAPI + websockets)

部署在 VPS 上的轻量 WebSocket 中继。

**技术栈：** Python 3.12+, FastAPI, websockets, PyJWT, firebase-admin

**职责：**
- WebSocket 连接管理（Hub 模式，每个用户一个 room）
- 消息路由：Desktop Agent ↔ Android App
- 离线消息队列（内存 dict，短期缓冲）
- JWT 认证
- FCM 推送触发

**API：**
| 端点 | 说明 |
|---|---|
| `GET /ws?token=JWT` | WebSocket 连接入口 |
| `POST /auth/login` | 获取 JWT |
| `POST /fcm/register` | 注册 FCM token |

**消息格式：**
```json
{
  "id": "uuid",
  "type": "event|command|ack",
  "from": "desktop|mobile",
  "sessionId": "...",
  "data": { ... },
  "ts": 1716883200
}
```

### 3. Android App (Kotlin + Jetpack Compose)

**核心页面：**
- **Session List** — 当前活跃会话 / 历史会话列表
- **Session Detail** — 聊天式 UI，展示消息、工具调用、工具输出
- **Permission Card** — 浮层/卡片，显示工具名、参数，提供 [允许] [拒绝] 按钮（带倒计时）

**核心模块：**
- `ConnectionManager` — 管理 WebSocket 连接，LAN 优先 / Cloud fallback
- `SessionRepository` — 本地缓存会话数据
- `NotificationHandler` — FCM 推送处理
- `BackgroundService` — 维持后台连接

**技术栈：**
- Compose + Material 3 + Navigation
- OkHttp (WebSocket)
- Kotlin Coroutines + Flow
- Room (本地 DB)
- Firebase Cloud Messaging

### 4. Claude Code Plugin（`.claude-plugin/` + `hooks/`）

系统以 **Claude Code 标准插件** 形式集成。用户 clone 项目后，通过 `--plugin-dir` 加载或安装到项目，不改动全局配置。

**插件结构：**
```
.claude-plugin/
└── plugin.json              # 插件清单

hooks/
└── hooks.json               # HTTP hook 配置（直接 POST 到 Desktop Agent）
```

**`.claude-plugin/plugin.json`：**
```json
{
  "name": "cli-notify",
  "displayName": "Claude Code Remote Control",
  "description": "Real-time session mirroring and remote control via Android app",
  "version": "1.0.0",
  "author": { "name": "cyanrain" },
  "hooks": "./hooks/hooks.json"
}
```

**`hooks/hooks.json` — 使用 HTTP hook 类型，无需 shell 脚本：**
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/session-start"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/user-prompt"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/pre-tool-use"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/post-tool-use"
          }
        ]
      }
    ],
    "PermissionRequest": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/permission-request"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/stop"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/session-end"
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt|idle_prompt",
        "hooks": [
          {
            "type": "http",
            "url": "http://localhost:19527/hook/notification"
          }
        ]
      }
    ]
  }
}
```

**关键设计决策：**
- **全部使用 `"type": "http"` hook**，Claude Code 直接将事件 JSON POST 到 Desktop Agent，无需 shell 脚本
- `UserPromptSubmit` hook 捕获用户从桌面 CLI 提交的每条消息，手机能实时看到对话全貌
- `PreToolUse` 不加 matcher 限制（所有工具调用都推送到手机），白名单在 Desktop Agent 侧处理
- `PermissionRequest` hook 在权限弹窗出现时补发通知——即使不通过它控制审批，也能提醒手机用户"可能需要关注桌面"
- `Notification` hook 匹配 `permission_prompt|idle_prompt`，感知 Claude 需要用户介入的时刻
- `Stop` hook 在 Claude 完成响应时触发，标记会话 idle，此时手机可安全注入回复
- 手机回复通过 `claude -p --continue --output-format stream-json` 注入，实时流式返回 Claude 的响应

## 事件流（Wire Protocol）

### Claude Code → Desktop Agent (HTTP hooks 标准 JSON)

Claude Code 通过 HTTP POST 发送标准 hook JSON，Desktop Agent 直接接收原生的 `hook_event_name`、`tool_name`、`tool_input` 等字段。

### Desktop Agent → Mobile (规范化 events)

```typescript
// 会话生命周期
{ type: "session.start", sessionId, timestamp, data: { cwd } }
{ type: "session.end", sessionId, timestamp }

// 消息
{ type: "message.user", sessionId, timestamp, data: { content } }
{ type: "message.assistant", sessionId, timestamp, data: { content, tokens? } }

// 工具调用
{ type: "tool.request", requestId, sessionId, timestamp, data: { toolName, params } }
{ type: "tool.result", requestId, sessionId, timestamp, data: { toolName, output, success } }
{ type: "tool.permission_request", sessionId, timestamp, data: { toolName, params, suggestions? } }

// 通知
{ type: "notification", sessionId, timestamp, data: { kind: "permission_prompt"|"idle_prompt" } }
```

### Mobile → Desktop Agent (commands)

```typescript
// 回复消息
{ type: "reply", sessionId, data: { content } }

// 审批
{ type: "approve", requestId, sessionId }
{ type: "deny", requestId, sessionId, data: { reason? } }

// 查询
{ type: "status", sessionId? }
{ type: "sync", sessionId }
```

## 反向操作实现

### 发送回复
手机发 `reply` 命令 → Desktop Agent 执行：
```bash
echo "phone message" | claude -p "回复用户" --continue --output-format stream-json
```
- `--output-format stream-json`：获取 Claude 的实时流式响应并转发到手机
- `--continue`：在最近会话中继续，保持上下文
- `--fork-session`（可选）：分支会话，手机回复不污染用户的主会话历史
- 仅在会话 idle 时注入（Stop hook 后），避免与正在处理中的 Claude 冲突

### 权限审批
PreToolUse HTTP hook 是**同步阻塞**的 — Desktop Agent 等待手机响应后才返回 HTTP 响应。流程：
1. 白名单工具 → 立即返回 `permissionDecision: "allow"`
2. 需要审批 → 推送手机，阻塞等待（最长 60s）
3. 手机 approve → 返回 `permissionDecision: "allow"`
4. 手机 deny → 返回 `permissionDecision: "deny"`
5. 超时 → 返回 `permissionDecision: "ask"`，Claude Code 退回桌面终端弹窗

## 超时 & 错误处理

- **权限审批超时**: 默认 60s，可配置（30/60/120），超时后退回桌面弹窗
- **WebSocket 断连**: 指数退避重连（1s → 2s → 4s → ... → 30s max）
- **手机未连接时**: 事件本地缓冲，手机连上后批量同步
- **Desktop Agent 未启动**: 手机显示"等待桌面连接..."，Cloud Relay 发送 push 提醒

## 安全

- Desktop Agent HTTP/WS 仅监听 localhost:19527，不暴露到公网
- Cloud Relay 用 JWT 认证
- 敏感工具审批必须显式确认（不允许静默通过）
- 消息内容不落盘到云端（Cloud Relay 仅内存转发，不持久化）

## 分阶段实施

**Phase 1 — MVP（局域网直连）**
- Claude Code plugin 结构 + HTTP hooks
- Desktop Agent HTTP + WS server
- Android Session List + Detail 页面
- 查看实时消息和工具调用
- 发送回复 (`claude --continue`)

**Phase 2 — 权限审批**
- PreToolUse 阻塞流程
- 手机端审批卡片（倒计时）
- 白名单配置
- 超时 fallback

**Phase 3 — 云端中继**
- Python FastAPI cloud relay server
- JWT 认证
- FCM 推送
- Android 后台连接服务 + 双通道自动切换

---

## 项目结构（规划）

```
cli-notify/
├── .claude-plugin/
│   └── plugin.json              # Claude Code 插件清单
├── hooks/
│   └── hooks.json               # HTTP hook 配置
├── desktop-agent/               # Node.js Desktop Agent
│   ├── src/
│   │   ├── server.ts            # HTTP + WS 服务入口
│   │   ├── hooks.ts             # Hook 事件处理
│   │   ├── relay.ts             # 云端中继客户端
│   │   ├── permissions.ts       # 权限审批管理
│   │   ├── sessions.ts          # Session 文件监听
│   │   └── config.ts            # 配置管理
│   ├── package.json
│   └── tsconfig.json
├── cloud-relay/                 # Python Cloud Relay Server
│   ├── main.py                  # FastAPI 入口
│   ├── hub.py                   # WebSocket Hub
│   ├── auth.py                  # JWT auth
│   ├── fcm.py                   # FCM push
│   └── requirements.txt
├── android/                     # Android App (Kotlin + Compose)
│   └── (Gradle project)
└── docs/
```
