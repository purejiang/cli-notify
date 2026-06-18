# Auth · Setup · E2EE 密钥交换

> 跨组件参考：插件 + 中继 + App 的认证、配对、QR 码、Setup 命令、E2EE 密钥交换、关键时序图

## 1. 认证体系

### 1.1 概述

```
配对密钥 (PAIRING_KEY)
    │
    ├── 桌面端 ──POST /auth/login──→ JWT(30天) + refresh_token(90天)
    │
    └── 手机端 ──POST /auth/pair───→ JWT(7天) + refresh_token(90天)
                                       │
                                       └── JWT 过期 ──POST /auth/refresh──→ 新JWT + 新refresh_token
```

- JWT: HS256，payload `{ user_id, role, exp, iat }`
- Refresh Token: 不透明字符串，SQLite 存储，一次性使用，消费后轮换
- user_id 固定为 `"desktop"`（桌面端和手机端共用同一房间）
- 所有请求通过 `Authorization: Bearer <jwt>` 或 `?token=<jwt>` 携带

### 1.2 端点

#### POST `/auth/login` (桌面端)

```json
// 请求
{ "user_id": "desktop", "secret": "<PAIRING_KEY>" }

// 200
{ "jwt": "eyJ...", "refresh_token": "abc...", "expires_in": 2592000 }

// 401
{ "code": "AUTH_FAILED", "message": "Invalid pairing key" }
```

#### POST `/auth/pair` (手机端)

```json
// 请求
{ "pairing_key": "<PAIRING_KEY>" }

// 200
{ "jwt": "eyJ...", "refresh_token": "abc...", "expires_in": 604800 }
```

#### POST `/auth/refresh`

```json
// 请求
{ "refresh_token": "abc..." }

// 200
{ "jwt": "eyJ...", "refresh_token": "new-abc...", "expires_in": 2592000 }

// 401 (已消费或过期)
{ "code": "AUTH_FAILED", "message": "Invalid or used refresh token" }
```

## 2. 桌面端 Setup 流程

### 触发

```bash
/cli-notify:setup https://relay.example.com:8765 my-pairing-key-12345
```

### 完整流程

```
1. POST /auth/login → { jwt, refresh_token }

2. 交互式配置:
   ├─ 审批模式: [A] 桌面  [B] 手机  [C] 混合 → 默认 A
   ├─ 审批超时(ms): → 默认 30000
   ├─ 超时策略: [A] 拒绝 [B] 允许 [C] 交回桌面 → 默认 C
   ├─ 数据截断上限(bytes): → 默认 51200
   ├─ 离线缓存: [A] 关闭 [B] 开启 → 默认 A
   ├─ 缓存上限(条): → 默认 1000
   └─ 扩展 Hook: 勾选 12 个中哪些 → 默认全不选

3. 写入 .cli-notify/config.json

4. 激活 hooks.json (从 hooks.json.disabled 或按配置生成)

5. GET /health 验证连通
```

### 配置存储

```
<project>/.cli-notify/
├── config.json         # JWT + 所有配置
├── hook-log.jsonl      # 运行时日志
└── offline_cache.jsonl # 离线缓存(可选)
```

```json
// config.json
{
  "relay_url": "https://relay.example.com:8765",
  "jwt": "eyJ...",
  "refresh_token": "abc...",
  "approval_mode": "desktop",
  "approval_timeout_ms": 30000,
  "fallback_action": "ask",
  "max_data_size": 51200,
  "offline_cache": false,
  "offline_cache_max": 1000,
  "core_hooks": ["SessionStart", "SessionEnd", ...],
  "extra_hooks": []
}
```

## 3. 手机端 Setup 流程

### 3.1 QR 码生成

中继启动时自动生成：

```
1. 读取/生成 PAIRING_KEY (如未设置则随机生成并打印)
2. QR 数据: {"relay_url":"https://relay.example.com:8765","pairing_key":"xxx"}
3. 终端输出 ASCII QR + HTTP /qr 端点提供 SVG QR
```

### 3.2 App 扫码配对

```
1. CameraX + ZXing 扫描 QR → 解析 relay_url + pairing_key

2. POST /auth/pair { pairing_key } → { jwt, refresh_token } → 存储 DataStore

3. WebSocket 连接 ws://relay_url/ws?token=<jwt>&role=mobile

4. 收到 auth_success

5. 自动 E2EE 密钥交换 (见 §4)

6. 收到 preferences + sessions 同步，开始接收实时消息
```

## 4. E2EE 密钥交换

### 4.1 时序

```
Mobile App                        Relay                    Desktop Plugin
    │                                │                          │
    │ 生成 P-256 密钥对(KeyStore)     │                          │
    │                                │                          │
    │ WS → key.exchange              │                          │
    │ { publicKey, algorithm }       │                          │
    │ ──────────────────────────────>│                          │
    │                                │ 验证公钥格式              │
    │                                │ 存储 SQLite public_keys  │
    │                                │                          │
    │ WS ← key.exchange response    │                          │
    │ { status:"ok", fingerprint }  │                          │
    │ <──────────────────────────────│                          │
    │                                │                          │
    │                                │  需要加密时               │
    │                                │  GET /pubkey ←────────── │
    │                                │  ──────────────────────> │
    │                                │                          │ 加密 data
    │  解密 data                     │                          │ POST /hook/relay
    │ <──────────────────────────────│ <────────────────────────│
```

### 4.2 参数

| 参数 | 值 |
|------|-----|
| 密钥交换 | ECDH P-256 |
| 对称加密 | AES-256-GCM |
| 密钥派生 | HKDF-SHA256 |
| Info String | `cli-notify-v2` |
| Salt | 32 字节零 |
| IV | 12 字节随机 |
| 临时公钥 | 65 字节未压缩点 |

### 4.3 密钥丢失处理

- App 重装 → 新密钥对 → 重新 key.exchange → 覆盖旧公钥
- 插件解密失败 → 清除 `config.json` 中缓存的 `phone_public_key` → GET `/pubkey` 重新获取

## 5. 关键时序图

### 5.1 审批往返 (app 模式)

```
Desktop Plugin            Relay                  Mobile App         User
     │                       │                       │                │
     │ PreToolUse Hook       │                       │                │
     │ POST /hook/relay      │                       │                │
     │ msgType:"request"     │                       │                │
     │ correlationId:"c1"    │                       │                │
     │ ────────────────────> │                       │                │
     │                       │ 验证 envelope 包装层    │                │
     │                       │ 创建 Future("c1")     │                │
     │                       │ WS 广播 ────────────> │ 通知+弹窗      │
     │                       │                       │ ──────────────>│
     │                       │                       │       点击"允许"│
     │                       │                       │ <──────────────│
     │                       │ WS ← response         │                │
     │                       │ correlationId:"c1"    │                │
     │                       │ _decision:{allow}     │                │
     │                       │ <──────────────────── │                │
     │                       │                       │                │
     │                       │ 匹配 Future("c1")     │                │
     │                       │ 提取 _decision        │                │
     │                       │                       │                │
     │ HTTP 200              │                       │                │
     │ { decision:"allow" }  │                       │                │
     │ <──────────────────── │                       │                │
     │                       │                       │                │
     │ stdout: permissionDecision:"allow"             │                │
     │ Hook 继续执行          │                       │                │
```

### 5.2 审批超时

```
Desktop Plugin            Relay                  Mobile App
     │                       │                       │
     │ POST (审批请求)        │                       │
     │ ────────────────────> │                       │
     │                       │ Future("c1") 创建     │
     │                       │ WS 广播 ────────────> │
     │                       │                       │
     │                       │ 每 5s 检查             │
     │                       │   mobile 全断？→ 立即  │
     │                       │   超时？→ 触发         │
     │                       │                       │
     │                       │ fallback_action="ask" │
     │                       │                       │
     │ HTTP 200              │                       │
     │ { decision:"ask" }    │                       │
     │ <──────────────────── │                       │
     │                       │                       │
     │ stdout: permissionDecision:"ask"              │
     │ → Claude Code 桌面弹窗，PC 用户决定             │
```

### 5.3 JWT 刷新 (插件端，并发安全)

```
Hook 进程 A                   Hook 进程 B                   Relay
     │                            │                           │
     │ POST /hook/relay           │ POST /hook/relay          │
     │ ────────────────────────>  │ ────────────────────────> │
     │ ← 401 ────────────────────│ ← 401 ─────────────────── │
     │                            │                           │
     │ 重读 config.json           │                           │
     │ jwt 还是旧的，自己刷新      │                           │
     │                            │                           │
     │ POST /auth/refresh         │                           │
     │ ────────────────────────────────────────────────────> │
     │ ← 200 { new_jwt, new_rt } │                           │
     │ 写入 config.json           │                           │
     │                            │                           │
     │ 用 new_jwt 重试            │ 重读 config.json           │
     │ POST /hook/relay ──────────────────────────────────>  │ 发现 jwt 已被 A 更新
     │ ← 200 ──────────────────────────────────────────────  │ 直接用 new_jwt，不再刷新
     │                            │                           │
     │                            │ 用 new_jwt 重试            │
     │                            │ POST /hook/relay ──────> │
     │                            │ ← 200                    │
```

**关键**：刷新前先重读 config.json，避免多个 Hook 进程竞争 refresh_token。

## 6. 端点汇总

| 方法 | 端点 | 认证 | 说明 |
|------|------|------|------|
| POST | `/auth/login` | Pairing Key | 桌面端登录 |
| POST | `/auth/pair` | Pairing Key | 手机端配对 |
| POST | `/auth/refresh` | Refresh Token | 刷新 JWT |
| POST | `/hook/relay` | JWT | 主转发端点 |
| GET | `/pubkey` | JWT | 获取 E2EE 公钥 |
| GET | `/qr` | 无 | SVG QR 码页面 |
| GET | `/health` | 无 | 健康检查 |
| WS | `/ws` | JWT (query) | WebSocket 连接 |
