# CLI-Notify 测试计划

> 版本: 1.0 | 日期: 2026-06-05 | 作者: 测试工程师

## 1. 概述

### 1.1 系统架构

```
Claude Code Desktop → Plugin (E2EE encrypt) → Cloud Relay (VPS, blind forward) → Android App (decrypt + display)
```

三端通信的核心协议由 `protocol/schema.json` 定义。所有组件必须遵循同一套 Envelope 消息格式。

### 1.2 测试范围

| 组件 | 语言/运行环境 | 测试框架 | 当前状态 |
|------|-------------|---------|---------|
| Protocol Schema | JSON Schema (draft-2020-12) | 自写 validator (零依赖) | 已覆盖 |
| Cloud Relay | TypeScript (Node.js, ESM) | Vitest v4 | 已覆盖 |
| Plugin | TypeScript (Node.js, ESM) | 自写 test runner (零依赖, tsx 运行) | 已覆盖 |
| Android | Kotlin (JVM) | JUnit + Gradle | 未运行 (环境限制) |

### 1.3 测试执行汇总

```
测试套件                      测试数    通过    失败
─────────────────────────────────────────────────────
Protocol Schema 验证            58      58      0
Relay E2EE 密钥校验             15      15      0
Relay Auth JWT                  20      20      0
Relay DB 持久化                 19      19      0
Relay Hub 路由                  32      32      0
Relay Errors 辅助                9       9      0
Plugin 数据提取 (enrich)        14      14      0
Plugin 富化逻辑 (Edit 行号)      3       3      0
Plugin Hook 映射                 9       9      0
Plugin Notification 白名单       1       1      0
Plugin E2EE 加密格式             5       5      0
Plugin Envelope 结构             4       4      0
Plugin→Relay E2E 集成            10      10      0
─────────────────────────────────────────────────────
总计                           199     199      0
```

## 2. 测试文件布局

```
cli-notify/
├── protocol/
│   └── schema.json                        # 协议定义（唯一真相源）
│
├── tests/
│   ├── fixtures/
│   │   ├── valid-envelopes.json           # 17 个合法 Envelope（覆盖所有 EventType）
│   │   └── invalid-envelopes.json         # 10 个非法 Envelope
│   ├── protocol/
│   │   └── schema-validation.test.js      # Schema 验证测试（零依赖，node 直接运行）
│   └── plugin/
│       └── integration.test.js            # Plugin 集成测试（零依赖，tsx 运行）
│
├── cloud-relay/
│   ├── vitest.config.ts                   # Vitest 配置（setupFiles: ./tests/setup.ts）
│   └── tests/
│       ├── setup.ts                       # 测试环境初始化（临时DB + 清理）
│       ├── e2ee.test.ts                   # P-256 密钥校验、指纹、算法验证
│       ├── auth.test.ts                   # JWT 签发/验证、Refresh Token 生命周期
│       ├── db.test.ts                     # 离线队列、会话、公钥、偏好设置
│       ├── hub.test.ts                    # 房间生命周期、消息路由、审批、偏好
│       └── errors.test.ts                 # 错误响应格式与 HTTP 状态码
│
└── docs/
    └── TEST_PLAN.md                       # 本文档
```

## 3. 已覆盖的测试场景

### 3.1 Protocol Schema 验证 (58 测试)

**文件:** `tests/protocol/schema-validation.test.js`
**运行:** `node tests/protocol/schema-validation.test.js`
**依赖:** 零外部依赖，内联 minimal JSON Schema validator

#### 3.1.1 有效 Envelope 验证 (17 项)

为 `protocol/schema.json` 中定义的每一种 EventType 创建了合法 Envelope：

- `session.start` — 携带 `data.cwd`
- `session.end` — 携带 `data.reason`
- `message.user` — 携带 `data.content`
- `message.assistant` — 携带 `data.{content, model, tokens, stopReason}`
- `tool.request` — 携带 `data.{toolName, params}`，msgType=request, correlationId 非 null
- `tool.result` — 携带 `data.{toolName, output, success}`
- `tool.permission_request` — 携带 `data.{toolName, message}`
- `notification` — 携带 `data.{kind, message, cwd}`
- `sync` — 请求（from: mobile）
- `key.exchange` — 请求 + KeyExchangeRequest 格式 `data.{publicKey, algorithm}`
- `set_preferences` — 请求（from: mobile）
- `get_preferences` — 请求（from: mobile）
- `preferences` — 事件（from: server）
- `auth_success` — 事件（from: server）
- `error` — 事件（from: server, 携带 ErrorResponse）
- `encrypted_envelope` — encrypted=true + EncryptedPayload 格式 `data.{ephemeralKey, iv, ciphertext}`
- `response_envelope` — msgType=response + correlationId 非 null

#### 3.1.2 非法 Envelope 拒绝 (10 项)

- 缺少必填字段: `type`, `id`, `sessionId`, `timestamp`
- 非法枚举值: `msgType` ("unknown_msg_type"), `from` ("browser"), `type` ("invalid.event.type")
- 类型错误: `data` 为 string（应为 object）, `encrypted` 为 string（应为 boolean）
- 约束违反: `timestamp` 为负数

#### 3.1.3 必填字段强制 (8 项)

逐一验证 8 个必填字段（type, id, msgType, sessionId, from, timestamp, encrypted, data）缺失时产生验证错误。

#### 3.1.4 枚举约束 (3 项)

- `MsgType`: 仅 `request` | `response` | `event` 合法
- `Peer`: 仅 `desktop` | `mobile` | `server` 合法
- `EventType`: 未知事件类型被拒绝

#### 3.1.5 EncryptedPayload (3 项)

- `encrypted` 必须为 boolean（非 string）
- `timestamp` >= 0 强制
- 交叉校验说明（Schema 层面不做 encrypted=true 与 EncryptedPayload 形状的交叉校验，由解密端验证）

#### 3.1.6 事件类型覆盖 (1 项)

- 运行时检查所有 15 个 EventType 在 fixtures 中均有代表

#### 3.1.7 ErrorResponse 格式 (5 项)

- 7 个 ErrorCode 枚举值全部存在于 schema 中
- code + message 为必填字段
- detail 为可选字段
- 缺失 code 或 message 时拒绝

#### 3.1.8 KeyExchange Schema (5 项)

- `KeyExchangeRequest`: publicKey 必填，algorithm const 校验 ("ECDH-P256-AES256-GCM")
- `KeyExchangeResponse`: status 枚举 ("ok"|"error")

---

### 3.2 Relay 单元测试 (95 测试)

**文件:** `cloud-relay/tests/*.test.ts`
**运行:** `cd cloud-relay && npx vitest run`
**环境:** 临时 SQLite 文件（`setup.ts` 生成随机路径，`afterAll` 清理）

#### 3.2.1 E2EE 密钥校验 (15 项) — `e2ee.test.ts`

- `isValidP256PublicKey`: 合法 65 字节 04 前缀密钥、非 base64 输入、过短/过长密钥、无 04 前缀、错误长度 + 正确前缀、全零格式密钥
- `computeKeyFingerprint`: 64 字符 hex 输出、确定性（同输入同输出）、不同密钥不同指纹、SHA-256 正确性
- `validateAlgorithm`: 精确匹配时返回算法、undefined 返回默认、不支持算法返回 null、空字符串返回默认、大小写敏感

#### 3.2.2 Auth JWT (20 项) — `auth.test.ts`

- `generateToken`: 生成三段式 JWT、不同用户不同 token、自定义过期时间
- `generateMobileToken`: 移动端 token 生成
- `verifyToken`: 合法 token 返回 userId、无效/过期/空字符串 token 返回 null、错误签名 token 拒绝、pairing key 旁路认证、错误 pairing key 拒绝
- Refresh Token: 生成并存储到 DB、合法 token 返回 userId、无效 token 返回 null、单次使用性（消费后二次使用失败）、不同用户独立 token、revokeRefreshToken 消费 token、revokeAllUserTokens 批量作废、同用户多 token 独立性

#### 3.2.3 DB 持久化 (19 项) — `db.test.ts`

- 离线队列: 入队/出队、FIFO 顺序、未知用户返回空数组、markDelivered 清除已投递、MAX_OFFLINE_QUEUE 上限裁剪（保留最新）、purgeDeliveredOlderThan 清理旧消息、不同用户隔离、已投递消息不再返回
- Session 元数据: upsert 创建、upsert 更新、未知用户返回空 map、endSession 设置 ended_at + status "ended"、同用户多 session 并行跟踪
- Public Keys: set + get 往返、替换已有密钥、未知用户返回 null
- User Preferences: 默认值返回、set + get 往返、覆盖更新

#### 3.2.4 Hub 路由 (32 项) — `hub.test.ts`

- 房间生命周期: 初始零房间、acceptAndJoin 创建房间、桌面/移动端独立跟踪、leave 清理并移除空房间、leave 保留有其他连接者的房间、桌面替换、未知 role 默认视为 mobile
- Envelope 构建: buildEnvelope 自动生成 id+timestamp、显式 encrypted 标记、sendEnvelope 发送 JSON 到 WebSocket、各字段正确性
- 公钥注册: 合法 P-256 注册、非法算法拒绝、非法格式拒绝、getPublicKeyRecord 检索、getPublicKey 返回裸 key 字符串、未知用户返回 null
- 偏好设置: 默认值、持久化后检索、非法 fallbackAction 降级到 "ask"、负 timeout 钳位到 0
- 消息路由: broadcastToUser 广播到所有连接（考虑 join 时 sync 消息基线）、routeMessage 转发 desktop→mobile 事件、routeMessage 转发 server→all 事件、routeMessage 转发响应、移动端离线检测、桌面在线检测
- 审批 Futures: createApprovalFuture 返回 Promise、resolveApproval 解析正确决策、未知请求返回 false、cleanupApproval 移除、不同用户隔离
- Session 元数据: setSessionMeta 创建记录、endSession 标记结束状态

#### 3.2.5 Errors 辅助 (9 项) — `errors.test.ts`

- sendError: 正确 HTTP 状态码和 body、无 detail 时省略
- buildError: 构建纯对象（无 Express 依赖）、无 detail 时省略
- 便利包装函数: invalidEnvelope (400)、unknownType (400)、authFailed (401)、roomNotFound (404)、requestTimeout (408)、rateLimited (429)、internal (500)

---

### 3.3 Plugin 集成测试 (38 测试)

**文件:** `tests/plugin/integration.test.js`
**运行:** `npx tsx tests/plugin/integration.test.js`
**依赖:** 零外部依赖，直接 import Plugin 源码模块

#### 3.3.1 数据提取 (enrich.ts) — 14 项

- SessionStart: `{ cwd }` 提取、缺失 cwd 默认空字符串
- UserPromptSubmit: `{ content }` 提取、缺失 prompt 默认空字符串
- PreToolUse: `{ toolName, params }` 提取
- PostToolUse: `{ toolName, output, success }` 提取（output 为 JSON stringify）
- PermissionRequest: `{ toolName, params, message }` 提取
- Stop: `{ content, model, tokens, stopReason }` 提取、纯空白 content 修剪为空
- SessionEnd: `{ reason }` 提取
- Notification: `{ kind, message, cwd }` 提取、非法 kind 默认 "idle_prompt"
- 未知 hook: 返回空对象

#### 3.3.2 Edit 行号富化 (enrich.ts) — 3 项

- 正常匹配: 在 originalFile 中定位 oldString，计算 oldLineStart/oldLineEnd
- 未找到: 返回 undefined editLineInfo
- 字段缺失: 缺少 originalFile 或 oldString 时返回 undefined

#### 3.3.3 Idle Notification 构建 (enrich.ts) — 2 项

- 正常 cwd: `{ kind: "idle_prompt", message: null, cwd }` 正确结构
- 空 cwd: cwd 为空字符串

#### 3.3.4 Hook 事件映射 (types.ts) — 9 项

- 8 种映射逐一验证: `SessionStart→session.start`, `UserPromptSubmit→message.user`, `PreToolUse→tool.request`, `PostToolUse→tool.result`, `PermissionRequest→tool.permission_request`, `Stop→message.assistant`, `SessionEnd→session.end`, `Notification→notification`
- 映射总数恰好为 8

#### 3.3.5 Notification 白名单 (types.ts) — 1 项

- `VALID_NOTIFICATION_KINDS` 包含 `permission_prompt`, `idle_prompt`, `auth_success`
- 不包含 `invalid`

#### 3.3.6 E2EE 加密 (crypto.ts) — 5 项

- EncryptedPayload 结构: `{ ephemeralKey, iv, ciphertext }` 均为 base64 字符串
- 临时密钥长度: 65 字节且 0x04 前缀
- IV 长度: 12 字节
- 随机性: 不同明文不同密文，每次调用不同 IV，每次调用不同 ephemeral key
- 边界: 空对象可加密、复杂嵌套数据可加密

#### 3.3.7 Envelope 结构 — 4 项

- SessionStart 信封: type/msgType/from/correlationId/encrypted/sessionId/id-UUID 均正确
- PreToolUse 信封: correlationId 等于 id（用于工具请求追踪）
- Stop 信封: type 为 "message.assistant"
- 全部 8 种 Hook 均产生有效信封

---

### 3.4 Plugin → Relay E2E 集成测试 (10 测试)

**方法:** 启动 Relay 后通过 curl 发送 Envelope 和 Plugin pipe 两种方式

#### 3.4.1 curl Envelope 直接发送 (3 项)

| 事件 | 响应 |
|------|------|
| session.start | `{"status":"ok","id":"..."}` |
| message.user | `{"status":"ok","id":"..."}` |
| message.assistant | `{"status":"ok","id":"..."}` |

#### 3.4.2 Plugin pipe 发送 (3 项)

| Hook stdin | exit code |
|-----------|-----------|
| SessionStart | 0 |
| UserPromptSubmit | 0 |
| Stop | 0 |

#### 3.4.3 错误场景 (4 项)

| 场景 | HTTP | 响应 |
|------|------|------|
| 错误 token | 401 | `{"code":"AUTH_FAILED",...}` |
| 缺少必填字段 | 400 | `{"code":"INVALID_ENVELOPE",...}` |
| 非法 msgType | 400 | `{"code":"INVALID_ENVELOPE",...}` |
| 非法 from | 400 | `{"code":"INVALID_ENVELOPE",...}` |

---

## 4. 未覆盖的测试范围

### 4.1 Android 端

- **状态:** 未运行
- **原因:** 环境中仅有 JVM 11，Gradle 要求 JVM 17+
- **建议:** 在 CI/CD 流水线或安装 Android Studio 的开发机上执行 `./gradlew test`
- **推荐测试框架:** JUnit 5 + MockK + Turbine (Flow 测试)
- **推荐覆盖:**
  - `CryptoManager` 解密正确性（与 Plugin 加密互通验证）
  - `EventParser` 信封解析（各种合法/非法输入）
  - `WebSocketClient` 连接/重连/断线逻辑
  - `ConnectionManager` 消息路由（desktop/mobile/server 多源分发）
  - `NotificationHelper` 本地通知触发
  - ViewModel 状态管理

### 4.2 Relay HTTP 端到端测试

- **状态:** 未实现
- **建议:** 使用 `supertest` + `vitest` 对 Express app 进行 HTTP 端点测试
- **推荐覆盖:**
  - `POST /auth/login` — 登录成功/失败/缺少字段
  - `POST /auth/pair` — 配对成功/失败
  - `POST /auth/refresh` — Token 刷新/过期/重用
  - `POST /hook/relay` — 覆盖所有 EventType 的 Envelope 提交

### 4.3 Relay WebSocket 集成测试

- **状态:** 当前使用 MockWebSocket（单元测），未做真实 WebSocket 测试
- **建议:** 使用 `ws` 客户端库在测试中连接 Relay 的 `/ws` 端点
- **推荐覆盖:**
  - WebSocket 认证（token 验证）
  - 消息双向转发（desktop↔mobile）
  - 离线队列投递（mobile 离线→上线后队列消息转储）
  - 断线清理（close/error 事件后 Room 清理）
  - 桌面替换（新桌面连接踢旧连接）

### 4.4 Plugin HTTP retry 逻辑

- **状态:** `relay.ts` 中的重试逻辑（指数退避+抖动）未测试
- **建议:** 使用 `msw` 或 `vitest` 的 `vi.fn()` mock `fetch`
- **推荐覆盖:**
  - 网络错误触发重试
  - 5xx 错误触发重试
  - 4xx 错误不重试
  - 429 使用 Retry-After header
  - 超时触发重试
  - 指数退避计算正确性
  - 最大重试次数耗尽后返回 false

### 4.5 E2EE 端到端加密互通

- **状态:** 仅验证了加密端（Plugin）的 EncryptedPayload 格式，未验证解密端（Android）能正确解密
- **建议:** 需要一个跨端测试：Plugin 加密一段数据 → Relay 透明转发 → Android 解密并验证原文
- **也可用 Node.js 模拟 Android 解密逻辑**（相同的 ECDH P-256 + HKDF + AES-256-GCM）

### 4.6 负载与性能测试

- **状态:** 未实现
- **建议工具:** k6, autocannon, 或自定义 Node.js 脚本
- **推荐场景:**
  - 并发 WebSocket 连接（100+ rooms）
  - 高频率消息投递（1000+ msg/s 单用户）
  - 大离线队列（500+ messages，验证数据库性能）
  - 长时间运行（24h+ 验证内存泄漏和 DB 增长）
  - 并发 hook/relay POST 请求

### 4.7 安全测试

- **状态:** 未实现
- **建议:**
  - JWT 安全性审查（alg=none 攻击、密钥强度）
  - Pairing key 熵值验证
  - SQL 注入扫描（better-sqlite3 的参数化查询覆盖）
  - WebSocket origin 校验
  - DDoS 防护（连接限制、消息速率限制）

---

## 5. 运行命令速查

```bash
# ── 全部测试 ──

# Protocol Schema 验证
node tests/protocol/schema-validation.test.js

# Relay 单元测试
cd cloud-relay && npx vitest run

# Plugin 集成测试
npx tsx tests/plugin/integration.test.js

# ── 端到端测试 (需手动启动 Relay) ──

# 启动测试 Relay
cd cloud-relay && npm run build
PAIRING_KEY="test-e2e-pairing-key-2026" \
  JWT_SECRET="e2e-test-jwt-secret" \
  SQLITE_PATH="./data/e2e-test.db" \
  node dist/main.js &

# curl Envelope 发送
curl -X POST "http://localhost:8765/hook/relay?token=test-e2e-pairing-key-2026" \
  -H "Content-Type: application/json" \
  -d '{"type":"session.start","id":"u-1","msgType":"event","correlationId":null,"sessionId":"s","from":"desktop","timestamp":1,"encrypted":false,"data":{"cwd":"/tmp"}}'

# Plugin pipe 发送
echo '{"hook_event_name":"SessionStart","session_id":"test-001","cwd":"/tmp/test"}' \
  | node cli-notify-plugin/scripts/relay-forward.mjs

# ── 编译验证 ──

cd cloud-relay && npm run build       # Relay (tsc)
cd cli-notify-plugin && npm run typecheck && npm run build  # Plugin
cd android && ./gradlew assembleDebug  # Android (需要 JVM 17+)
```

---

## 6. 已知 Bug 及修复

### Bug #1: `getPublicKeyRecord` SQL 列名映射缺失

- **文件:** `cloud-relay/src/db.ts:234-242`
- **严重程度:** 中（影响 `/pubkey` 端点和公钥路由）
- **描述:** SQL 查询返回蛇形列名 (`user_id`, `public_key`, `created_at`)，但 TypeScript 接口 `PublicKeyRecord` 使用驼峰命名 (`userId`, `publicKey`, `createdAt`)。未做别名映射导致返回对象的 `publicKey` 字段为 `undefined`。
- **修复:** 在 SQL 中添加 `as` 别名 `user_id as userId, public_key as publicKey, created_at as createdAt`
- **状态:** 已修复

### Bug #2: Plugin → Relay 协议不匹配（历史）

- **描述:** Plugin 将 HookInput 转换为 Envelope 后 POST，但 Relay 的 `/hook/relay` 期望原始 HookInput（snake_case）。
- **修复:** Relay 更新为直接接收 Envelope 格式，Plugin 保持不变（始终 POST Envelope）。hooks.ts 从 `handleRelay()` 简化为 `handleRelayMetadata()`（仅对未加密事件提取会话元数据）。
- **状态:** 已修复
