# CLI-Notify Protocol & Architecture Design

**Date:** 2026-06-05  
**Status:** Approved  
**Author:** purejiang (Team Lead)

---

## 1. Overview

CLI-Notify is a real-time Claude Code session mirroring and remote notification system. This document defines the v1 protocol and architecture for the refactored system.

### Architecture

```
Claude Code Desktop ──HTTP hooks──▶ Plugin (relay-forward.ts)
                                        │ E2EE encrypt
                                        ▼
                                  Cloud Relay (VPS)
                                        │ blind forward
                                        ▼
                                  Android App (decrypt + display)
```

### Design Principles

1. **Relay is an opaque pipe** — routes messages by envelope, never decrypts data
2. **Schema-driven** — JSON Schema is the single source of truth for all message types
3. **Message bus semantics** — every message is a Request, Response, or Event
4. **E2EE by default** — all user content is encrypted; only metadata is visible to relay

---

## 2. Protocol: Envelope

Every message wraps in a standard envelope:

```json
{
  "type": "message.assistant",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "msgType": "event",
  "correlationId": null,
  "sessionId": "session-uuid",
  "from": "desktop",
  "timestamp": 1718123456789,
  "encrypted": true,
  "data": { ... }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Event type discriminator (see §3) |
| `id` | UUID | yes | Unique message identifier |
| `msgType` | enum | yes | `request` \| `response` \| `event` |
| `correlationId` | UUID\|null | yes | Links request↔response; null for events |
| `sessionId` | string | yes | Claude Code session identifier |
| `from` | enum | yes | `desktop` \| `mobile` \| `server` |
| `timestamp` | int64 | yes | Unix epoch milliseconds |
| `encrypted` | boolean | yes | Whether data is E2EE encrypted |
| `data` | object | yes | Payload (EncryptedPayload or typed data) |

### Message Bus Semantics

| msgType | Behavior | Requires correlationId | Timeout |
|---------|----------|----------------------|---------|
| `event` | Fire and forget. No response expected. | No | N/A |
| `request` | Expects exactly one `response` | Yes (auto-generated) | 30s default |
| `response` | Answers a `request` | Yes (matches request id) | N/A |

---

## 3. Event Catalog

### 3.1 Events (msgType: "event")

| type | Direction | data schema | Encrypted |
|------|-----------|-------------|-----------|
| `session.start` | desktop→mobile | `{ cwd: string }` | Yes |
| `session.end` | desktop→mobile | `{ reason: string }` | Yes |
| `message.user` | desktop→mobile | `{ content: string }` | Yes |
| `message.assistant` | desktop→mobile | `{ content, model, tokens?, stopReason }` | Yes |
| `tool.request` | desktop→mobile | `{ toolName, params }` | Yes |
| `tool.result` | desktop→mobile | `{ toolName, output?, success }` | Yes |
| `tool.permission_request` | desktop→mobile | `{ toolName, params?, message }` | Yes |
| `notification` | server→mobile | `{ kind, message?, cwd? }` | No |
| `sync` | server→mobile | `{ sessions: [...] }` | No |
| `auth_success` | server→mobile | `{ user_id: string }` | No |

### 3.2 Requests & Responses

| type | Direction | data (request) | data (response) |
|------|-----------|----------------|-----------------|
| `key.exchange` | mobile→server | `{ publicKey }` | `{ status: "ok" }` |
| `set_preferences` | mobile→server | `{ approvalTimeoutMs, fallbackAction }` | `{ status: "ok" }` |
| `get_preferences` | mobile→server | `{}` | `{ approvalTimeoutMs, fallbackAction, hasDesktop }` |
| `sync` | mobile→server | `{}` | `{ sessions: [...] }` |

### 3.3 Error Response

When a request fails, the response has `type: "error"`:

```json
{
  "code": "AUTH_FAILED",
  "message": "JWT expired",
  "detail": {}
}
```

Error codes: `INVALID_ENVELOPE`, `UNKNOWN_TYPE`, `AUTH_FAILED`, `ROOM_NOT_FOUND`, `REQUEST_TIMEOUT`, `RATE_LIMITED`, `INTERNAL`

---

## 4. E2EE Encryption

### 4.1 Key Exchange (once per connection)

```
Mobile                                    Relay                                   Desktop(Plugin)
  │──── key.exchange request ────▶│                                          │
  │                               │──── store publicKey ────▶                │
  │                               │        (in memory)                       │
  │                               │                     Desktop fetches via  │
  │                               │          ◀── GET /pubkey?token=... ──────│
  │                               │──── publicKey ─────────────────────────▶│
  │◀── key.exchange response ────│                                          │
```

### 4.2 Per-Message Encryption

- **Algorithm:** ECDH P-256 (ephemeral key) + HKDF-SHA256 → AES-256-GCM
- **EncryptedPayload:** `{ ephemeralKey, iv, ciphertext }`
- **Each message uses a fresh ephemeral key** for forward secrecy
- **HKDF info string:** `"cli-notify-v1"` (must match between desktop + mobile)

### 4.3 Who Encrypts

- **Plugin (desktop side)** encrypts data before POST to relay
- Plugin fetches mobile's public key from `GET /pubkey`
- Plugin generates fresh ephemeral key per message
- Relay **never** has access to plaintext

---

## 5. Relay Server

### 5.1 Layered Architecture

```
Transport (Express + ws)
    → Router (envelope validation + dispatch)
        → Hub (connection lifecycle + delivery)
            → Store (SQLite persistence)
```

### 5.2 API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | pairing key | Desktop login → JWT |
| POST | `/auth/pair` | pairing key | Mobile pair → JWT + refresh token |
| POST | `/auth/refresh` | refresh token | Rotate JWT + refresh token |
| GET | `/pubkey` | JWT | Get mobile's public key |
| POST | `/hook/relay` | JWT | Unified hook endpoint (all events) |
| GET | `/health` | none | Health check |

### 5.3 SQLite Schema

```sql
-- Offline message queue
CREATE TABLE offline_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL,
  message    TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

-- Session metadata
CREATE TABLE sessions (
  session_id    TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  cwd           TEXT DEFAULT '',
  status        TEXT DEFAULT 'active',
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER,
  message_count INTEGER DEFAULT 0
);

-- User preferences
CREATE TABLE preferences (
  user_id             TEXT PRIMARY KEY,
  approval_timeout_ms INTEGER DEFAULT 8000,
  fallback_action     TEXT DEFAULT 'ask'
);
```

### 5.4 File Structure

```
cloud-relay/src/
├── main.ts       # Entry: HTTP + WS bootstrap
├── routes.ts     # Express route definitions
├── router.ts     # NEW: envelope validation + dispatch
├── hub.ts        # REFACTORED: connection + delivery only
├── store.ts      # NEW: SQLite persistence
├── auth.ts       # JWT + refresh tokens
├── config.ts     # Environment config
├── errors.ts     # NEW: structured error types
└── types.ts      # NEW: generated from protocol/schema.json
```

---

## 6. Android App

### 6.1 Layered MVVM

```
UI (Compose) → ViewModel → Domain (MessageBus + EventRouter) → Data (Protocol + WS + Crypto)
```

### 6.2 Key New Components

- **MessageBus** — client-side request/response correlation with timeout
- **Protocol.kt** — generated from JSON Schema (replaces hand-written SessionEvent.kt)
- **EventRouter** — dispatches events to ViewModels by type + sessionId

### 6.3 UI Design

- Dark theme (Material 3)
- Session list with status indicators
- Message bubbles with Markdown rendering + code syntax highlighting
- Tool call cards (collapsible)
- Local push notifications (NotificationManager, not FCM)

### 6.4 File Changes

| File | Action |
|------|--------|
| `data/model/Protocol.kt` | NEW (generated) |
| `domain/MessageBus.kt` | NEW |
| `domain/EventRouter.kt` | NEW |
| `data/ws/EventParser.kt` | REFACTOR |
| `data/ConnectionManager.kt` | REFACTOR |
| `data/ws/WebSocketClient.kt` | MINOR |
| `data/crypto/CryptoManager.kt` | REFACTOR |
| `ui/**` (~8 files) | REFACTOR |
| `data/model/SessionEvent.kt` | REMOVE |

---

## 7. Plugin

### 7.1 File Structure

```
cli-notify-plugin/
├── .claude-plugin/plugin.json
├── hooks/hooks.json
├── commands/setup.md
├── scripts/
│   ├── relay-config.json
│   ├── relay-forward.ts    # NEW: TypeScript relay script
│   └── types.ts            # NEW: generated from schema
├── package.json             # NEW
├── tsconfig.json            # NEW
└── README.md
```

### 7.2 relay-forward.ts Flow

1. Read hook context from stdin (JSON)
2. Map hook event name → protocol event type
3. Fetch mobile public key from `GET /pubkey` (cached)
4. Build envelope
5. ECDH encrypt data field with ephemeral key
6. POST to `POST /hook/relay`
7. Retry (3x, exponential backoff) on failure

### 7.3 Hook → Event Type Mapping

| Claude Code Hook | Event Type | Data extracted |
|-----------------|------------|----------------|
| SessionStart | `session.start` | cwd |
| UserPromptSubmit | `message.user` | prompt → content |
| PreToolUse | `tool.request` | tool_name, tool_input |
| PostToolUse | `tool.result` | tool_name, tool_response |
| PermissionRequest | `tool.permission_request` | tool_name, tool_input |
| Stop | `message.assistant` | stop_reason, last_assistant_message |
| SessionEnd | `session.end` | reason |
| Notification | `notification` | notification_type, message |

---

## 8. Scope & Decisions

### In Scope (v1)
- Unified protocol with JSON Schema
- Message bus semantics (request/response/event)
- E2EE with ECDH + AES-256-GCM
- Relay persistence (SQLite)
- Android UI redesign
- Local push notifications
- Plugin TypeScript rewrite

### Deferred (future)
- Remote approval (approve/deny from mobile)
- FCM push notifications
- Multi-user support
- Message editing/deletion
- File attachment support

### Key Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Transport | WebSocket | Already working, minimal change |
| Schema | JSON Schema | Human-readable, good tooling for both Kotlin + TS |
| E2EE | ECDH P-256 + AES-256-GCM | Already partially implemented, standard algorithms |
| Encryption point | Plugin (desktop side) | Keeps relay as opaque pipe |
| Persistence | SQLite (better-sqlite3) | Lightweight, zero-config, sync API fits Node.js model |
| Rich text | Android-side only | Relay can't see content (E2EE) |
| Push | Local notifications | No FCM dependency, simpler |
| Code generation | Gradle task (Android) + script (TS) | Standard tooling for each platform |

---

## 9. Schema Artifact

The formal JSON Schema is at `protocol/schema.json` in the repository root. It is the single source of truth for all message types. All components generate their types from this schema.

### Code Generation

**TypeScript (Relay + Plugin):**
```bash
npx json2ts protocol/schema.json -o cloud-relay/src/types.ts
```

**Kotlin (Android):**
```bash
# Gradle task (to be added to android/app/build.gradle)
./gradlew generateProtocolTypes
# Output: app/src/main/java/com/clinotify/data/model/Protocol.kt
```
