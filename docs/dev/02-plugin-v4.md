# Phase 2: 插件端 v4 开发详细设计

> 依赖：Phase 1 Protocol v2 完成
> 产出：7 个 Python 模块 + hooks.json + setup.md + 测试

## 1. 模块拆分

现有 `relay-forward.py` 近 800 行过于庞大，拆分为 7 个独立模块：

```
cli-notify-plugin/scripts/
├── relay_forward.py      # 主入口 (~200行)
├── hook_processor.py     # Hook 映射与数据提取 (~300行)
├── envelope_builder.py   # Envelope 构建、turn_id 管理、截断、合并 (~180行)
├── encryptor.py          # E2EE 加解密 (~120行)
├── config_manager.py     # 配置读写 (~80行)
├── offline_cache.py      # 离线缓存 JSONL (~80行)
└── relay_client.py       # HTTP POST + 重试 + JWT刷新 (~180行)
```

### 1.1 主入口: `relay_forward.py`

```python
"""
CLI-Notify Hook Forwarder v4
读取 stdin Hook JSON → 处理 → POST Relay → stdout 决策
"""
import sys, json, os
from hook_processor import process_hook
from envelope_builder import build_envelope, build_decision_response, TurnManager, MessageBuffer
from config_manager import ConfigManager
from relay_client import RelayClient


def main():
    config = ConfigManager.load()
    body = json.loads(sys.stdin.read())

    # 1. 映射 Hook → HookEvent
    event = process_hook(body, config)
    if event is None:
        # 未启用的 Hook，静默通过
        _output({"continue": True})
        return

    # 2. 构建 Envelope（含截断、合并、E2EE 加密）
    envelope = build_envelope(body, event, config)
    if envelope is None:
        # MessageBuffer 还在合并窗口内
        _output({"continue": True})
        return

    # 3. 发送到中继
    client = RelayClient(config)

    if event.msg_type == "request":
        # 审批/Elicitation：同步等待 relay HTTP 响应
        response = client.post_and_wait(envelope, timeout=config.approval_timeout_ms)
        decision = build_decision_response(body, response, config)
        _output(decision)
    else:
        # 单向事件：fire-and-forget
        client.post(envelope)
        _output({"continue": True})


def _output(data: dict):
    """输出 JSON 到 stdout（Hook 读取）"""
    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 永不阻塞 Claude Code — 异常统一 catch，exit 0
        print(f"[cli-notify] {e}", file=sys.stderr)
        _output({"continue": True})
    finally:
        sys.exit(0)  # SessionEnd 超时 1.5s 需要快速退出
```

**关键设计点：**
- 全局 try/except → exit 0（永不阻塞）
- stdout 仅输出 JSON（Hook 协议要求）
- stderr 仅日志
- SessionEnd 1.5s 超时保护

### 1.2 Hook 处理器: `hook_processor.py`

```python
"""Hook 映射：Hook 名 → EventType，数据提取，msgType 判定"""
from dataclasses import dataclass
from typing import Optional

@dataclass
class HookEvent:
    event_type: str         # snake_case EventType
    msg_type: str           # "event" | "request"
    raw: dict               # Hook stdin JSON 全量
    tool_use_id: Optional[str]
    agent_id: Optional[str]
    message_id: Optional[str]
    task_id: Optional[str]
    session_id: str
    cwd: str

# 完整 30 个 Hook → EventType 映射
HOOK_TYPE_MAP = {
    "SessionStart":        "session_start",
    "SessionEnd":          "session_end",
    "UserPromptSubmit":    "user_prompt_submit",
    "UserPromptExpansion": "user_prompt_expansion",
    "PreToolUse":          "pre_tool_use",
    "PostToolUse":         "post_tool_use",
    "PostToolUseFailure":  "post_tool_use_failure",
    "PostToolBatch":       "post_tool_batch",
    "PermissionRequest":   "permission_request",
    "PermissionDenied":    "permission_denied",
    "Stop":                "stop",
    "StopFailure":         "stop_failure",
    "Notification":        "notification",
    "MessageDisplay":      "message_display",
    "SubagentStart":       "subagent_start",
    "SubagentStop":        "subagent_stop",
    "TaskCreated":         "task_created",
    "TaskCompleted":       "task_completed",
    "Elicitation":         "elicitation",
    "ElicitationResult":   "elicitation_result",
    "TeammateIdle":        "teammate_idle",
    "Setup":               "setup",
    "PreCompact":          "pre_compact",
    "PostCompact":         "post_compact",
    "ConfigChange":        "config_change",
    "CwdChanged":          "cwd_changed",
    "FileChanged":         "file_changed",
    "InstructionsLoaded":  "instructions_loaded",
    "WorktreeCreate":      "worktree_create",
    "WorktreeRemove":      "worktree_remove",
}

# 审批模式为 app/hybrid 时使用 request
REQUEST_HOOKS = {"PreToolUse", "PermissionRequest", "Elicitation"}


def process_hook(body: dict, config) -> Optional[HookEvent]:
    """
    处理 Hook stdin JSON，返回 HookEvent 或 None（未启用/未知 Hook）
    """
    hook_name = body.get("hook_event_name", "")

    # 映射 EventType
    event_type = HOOK_TYPE_MAP.get(hook_name)
    if event_type is None:
        # 未知 Hook → snake_case 下作为兜底事件发送
        event_type = _to_snake_case(hook_name) if hook_name else "unknown_hook"

    # 检查是否启用
    if not _is_enabled(hook_name, config):
        return None

    # 提取 ID 字段（直接从 Hook 数据取，不做复杂解析）
    tool_use_id = body.get("tool_use_id")
    agent_id = body.get("agent_id")
    message_id = body.get("message_id")
    task_id = body.get("task_id")

    # 确定 msgType
    msg_type = "event"
    if hook_name in REQUEST_HOOKS and config.approval_mode in ("app", "hybrid"):
        msg_type = "request"

    return HookEvent(
        event_type=event_type,
        msg_type=msg_type,
        raw=body,
        tool_use_id=tool_use_id,
        agent_id=agent_id,
        message_id=message_id,
        task_id=task_id,
        session_id=body.get("session_id", ""),
        cwd=body.get("cwd", ""),
    )


def _to_snake_case(name: str) -> str:
    """PascalCase → snake_case"""
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _is_enabled(hook_name: str, config) -> bool:
    if hook_name in config.core_hooks:
        return True
    if hook_name in config.extra_hooks:
        return True
    return False
```

### 1.3 Envelope 构建器: `envelope_builder.py`

```python
"""Envelope v2 构建、turn_id 管理、数据截断、MessageDisplay 合并"""
import uuid, time, json, os
from typing import Optional
from encryptor import encrypt_envelope


class TurnManager:
    """管理对话轮次 turn_id 的生命周期"""
    def __init__(self):
        self._current: Optional[str] = None

    def start(self) -> str:
        self._current = str(uuid.uuid4())
        return self._current

    def end(self):
        self._current = None

    @property
    def current(self) -> Optional[str]:
        return self._current


class MessageBuffer:
    """MessageDisplay delta 合并：50ms 窗口内收集 → 合并后发送"""
    def __init__(self, window_ms: int = 50):
        self.window_s = window_ms / 1000.0
        self._buffer: list[dict] = []
        self._message_id: Optional[str] = None
        self._last_flush: float = 0.0

    def add(self, body: dict) -> Optional[dict]:
        now = time.time()
        mid = body.get("message_id", "")

        # 新的 message_id → 立即发送旧 buffer
        if mid != self._message_id and self._buffer:
            result = self._merge()
            self._buffer = [body]
            self._message_id = mid
            self._last_flush = now
            return result

        self._buffer.append(body)
        self._message_id = mid

        if now - self._last_flush >= self.window_s:
            return self._merge()

        return None

    def flush(self) -> Optional[dict]:
        return self._merge()

    def _merge(self) -> Optional[dict]:
        if not self._buffer:
            return None
        merged_text = "".join(b.get("delta", "") for b in self._buffer)
        base = dict(self._buffer[0])
        base["delta"] = merged_text
        base["_merged_count"] = len(self._buffer)
        self._buffer.clear()
        return base


# 模块级单例（进程生命周期）
_turn_mgr = TurnManager()
_msg_buffer = MessageBuffer(window_ms=50)


def build_envelope(body: dict, event, config) -> Optional[dict]:
    """构建 Protocol v2 Envelope"""
    hook_name = body.get("hook_event_name", "")

    # --- turn_id 管理 ---
    if hook_name == "UserPromptSubmit":
        turn_id = _turn_mgr.start()
    elif hook_name in ("Stop", "StopFailure", "SessionEnd"):
        turn_id = _turn_mgr.current
        _turn_mgr.end()
        # SessionEnd 时强制 flush MessageBuffer
        flushed = _msg_buffer.flush()
        if flushed:
            _send_flushed(flushed, event, config)
    else:
        turn_id = _turn_mgr.current

    # --- MessageDisplay 合并 ---
    raw = dict(body)  # 浅拷贝
    if hook_name == "MessageDisplay":
        merged = _msg_buffer.add(raw)
        if merged is None:
            return None  # 在窗口内，暂不发送
        raw = merged

    # --- 二进制数据兜底 ---
    try:
        raw_str = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        import base64
        raw_bytes = json.dumps(raw, ensure_ascii=False, default=str).encode('utf-8', errors='replace')
        raw = {"_raw_b64": base64.b64encode(raw_bytes).decode()}
        raw_str = json.dumps(raw, ensure_ascii=False)

    # --- 数据截断 ---
    truncated = False
    data_bytes = raw_str.encode('utf-8')
    if len(data_bytes) > config.max_data_size:
        raw = _truncate_raw(raw, config.max_data_size)
        truncated = True

    # --- 构建 envelope ---
    envelope = {
        "type": event.event_type,
        "id": str(uuid.uuid4()),
        "msgType": event.msg_type,
        "sessionId": event.session_id,
        "from": "desktop",
        "timestamp": int(time.time() * 1000),
        "encrypted": False,
        "data": {
            "raw": raw,
            "tool_use_id": event.tool_use_id,
            "agent_id": event.agent_id,
            "turn_id": turn_id,
            "truncated": truncated,
        },
        "correlationId": event.tool_use_id or event.message_id or event.task_id,
        "groupId": event.agent_id,
    }

    # --- E2EE 加密 ---
    if config.e2ee_enabled and config.phone_public_key:
        envelope = encrypt_envelope(envelope, config.phone_public_key)

    return envelope


def build_decision_response(body: dict, relay_response: Optional[dict], config) -> dict:
    """构建 Hook 决策 JSON（stdout 输出）"""
    hook_name = body.get("hook_event_name", "")

    if relay_response is None:
        # 超时/网络错误 → fallback
        fallback = config.fallback_action
        decision = fallback if fallback in ("deny", "allow") else "ask"
        reason = f"审批超时({config.approval_timeout_ms}ms)，fallback: {fallback}"
    else:
        decision = relay_response.get("decision", "deny")
        reason = relay_response.get("reason", "")

    if hook_name == "PreToolUse":
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,  # "allow" | "deny" | "ask"
                "permissionDecisionReason": reason,
            },
        }
    elif hook_name == "PermissionRequest":
        behavior = "allow" if decision == "allow" else "deny"
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": behavior},
                "message": reason,
            },
        }
    elif hook_name == "Elicitation":
        action = "accept" if decision == "allow" else "decline"
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "Elicitation",
                "action": action,
            },
        }
    return {"continue": True}


def _truncate_raw(raw: dict, max_bytes: int) -> dict:
    """截断 raw JSON 至 max_bytes"""
    raw_str = json.dumps(raw, ensure_ascii=False)
    truncated_bytes = raw_str.encode('utf-8')[:max_bytes]
    truncated_str = truncated_bytes.decode('utf-8', errors='replace')
    try:
        result = json.loads(truncated_str)
    except json.JSONDecodeError:
        result = {"_truncated_text": truncated_str[:500]}
    result["_truncated"] = True
    result["_original_size"] = len(raw_str.encode('utf-8'))
    return result


def _send_flushed(merged: dict, event, config):
    """发送被强制 flush 的 MessageDisplay 合并数据"""
    # 构建一个独立的 envelope 并发送
    # ... (实现略)
    pass
```

### 1.4 E2EE 加密: `encryptor.py`

```python
"""E2EE: ECDH P-256 + AES-256-GCM + HKDF-SHA256 (info='cli-notify-v2')"""
import os, json, base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend

HKDF_INFO = b"cli-notify-v2"
HKDF_SALT = b"\x00" * 32


def encrypt_envelope(envelope: dict, peer_public_key_b64: str) -> dict:
    """加密 envelope.data，替换为 EncryptedPayload"""
    # 生成临时 P-256 密钥对
    eph_priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
    eph_pub = eph_priv.public_key()
    eph_pub_bytes = eph_pub.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    # 解析对方公钥
    peer_pub_bytes = base64.b64decode(peer_public_key_b64)
    peer_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), peer_pub_bytes
    )

    # ECDH
    shared = eph_priv.exchange(ec.ECDH(), peer_pub)

    # HKDF
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=HKDF_SALT, info=HKDF_INFO,
        backend=default_backend(),
    ).derive(shared)

    # AES-256-GCM 加密
    iv = os.urandom(12)
    aesgcm = AESGCM(derived)
    plaintext = json.dumps(envelope["data"], ensure_ascii=False).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, None)

    # 替换 data
    envelope["data"] = {
        "ephemeralKey": base64.b64encode(eph_pub_bytes).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
    envelope["encrypted"] = True
    return envelope


def decrypt_payload(encrypted_data: dict, private_key) -> dict:
    """解密 EncryptedPayload → 原始 data dict（App 端/插件端接收时使用）"""
    eph_key_bytes = base64.b64decode(encrypted_data["ephemeralKey"])
    iv = base64.b64decode(encrypted_data["iv"])
    ciphertext = base64.b64decode(encrypted_data["ciphertext"])

    eph_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), eph_key_bytes
    )
    shared = private_key.exchange(ec.ECDH(), eph_pub)

    derived = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=HKDF_SALT, info=HKDF_INFO,
        backend=default_backend(),
    ).derive(shared)

    aesgcm = AESGCM(derived)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))
```

### 1.5 配置管理: `config_manager.py`

```python
"""配置管理：.cli-notify/config.json"""
import json, os

DEFAULT_CONFIG = {
    "relay_url": "",
    "jwt": "",
    "refresh_token": "",
    "approval_mode": "desktop",
    "approval_timeout_ms": 30000,
    "fallback_action": "ask",
    "max_data_size": 51200,
    "offline_cache": False,
    "offline_cache_max": 1000,
    "e2ee_enabled": True,
    "phone_public_key": None,
    "core_hooks": [
        "SessionStart", "SessionEnd", "UserPromptSubmit",
        "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
        "PermissionRequest", "PermissionDenied",
        "Stop", "StopFailure", "Notification", "MessageDisplay",
        "SubagentStart", "SubagentStop",
        "TaskCreated", "TaskCompleted",
        "Elicitation",
    ],
    "extra_hooks": [],
}


class ConfigDict(dict):
    """支持 .attr 访问的 dict"""
    def __getattr__(self, key):
        if key not in self:
            raise AttributeError(f"Unknown config key: {key}")
        return self[key]


class ConfigManager:
    @staticmethod
    def path() -> str:
        # 注意：插件进程中 cwd 不一定可靠
        # 优先使用 CLAUDE_PROJECT_DIR 环境变量
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
        return os.path.join(project_dir, ".cli-notify", "config.json")

    @staticmethod
    def load() -> ConfigDict:
        config = DEFAULT_CONFIG.copy()
        try:
            with open(ConfigManager.path(), "r") as f:
                config.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return ConfigDict(config)

    @staticmethod
    def save(config: dict):
        os.makedirs(os.path.dirname(ConfigManager.path()), exist_ok=True)
        with open(ConfigManager.path(), "w") as f:
            json.dump(dict(config), f, indent=2, ensure_ascii=False)
```

### 1.6 离线缓存: `offline_cache.py`

```python
"""离线缓存：断连时暂存到 JSONL 文件，重连后按序重放"""
import json, os, time


class OfflineCache:
    def __init__(self, path: str, max_size: int = 1000):
        self.path = path
        self.max_size = max_size

    def append(self, envelope: dict):
        """追加一条消息。超出上限则 FIFO 淘汰"""
        entry = {
            "seq": int(time.time() * 1_000_000),
            "envelope": envelope,
        }
        entries = self._read_all()
        entries.append(entry)
        if len(entries) > self.max_size:
            entries = entries[-self.max_size:]
        self._write_all(entries)

    def pop_all(self) -> list[dict]:
        """取出所有缓存（时序排序），清空文件"""
        entries = self._read_all()
        entries.sort(key=lambda e: e["seq"])
        envelopes = [e["envelope"] for e in entries]
        self._write_all([])
        return envelopes

    def size(self) -> int:
        return len(self._read_all())

    def _read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def _write_all(self, entries: list[dict]):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
```

### 1.7 HTTP 客户端: `relay_client.py`

```python
"""Relay HTTP 客户端：POST + 重试 + JWT 刷新 + 离线缓存"""
import httpx, time, random, sys, os
from config_manager import ConfigManager

NON_RETRYABLE = {400, 403, 404, 405, 409, 410, 422}  # 401 移出，允许刷新后重试
MAX_RETRIES = 3


class RelayClient:
    def __init__(self, config):
        self.config = config
        self.client = httpx.Client(timeout=httpx.Timeout(30.0))

    def post(self, envelope: dict) -> bool:
        """POST /hook/relay (fire-and-forget)，返回是否成功"""
        url = f"{self.config.relay_url}/hook/relay"
        headers = {"Authorization": f"Bearer {self.config.jwt}"}

        for attempt in range(MAX_RETRIES):
            try:
                resp = self.client.post(url, json=envelope, headers=headers)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 401 and self._try_refresh():
                    headers["Authorization"] = f"Bearer {self.config.jwt}"
                    continue
                if resp.status_code in NON_RETRYABLE:
                    _log(f"Non-retryable: {resp.status_code}")
                    return False
            except (httpx.RequestError, httpx.TimeoutException) as e:
                _log(f"Network error (attempt {attempt + 1}): {e}")

            if attempt < MAX_RETRIES - 1:
                delay = min((2 ** attempt) + random.random(), 30)
                time.sleep(delay)

        # 全部重试失败 → 离线缓存
        if self.config.offline_cache:
            from offline_cache import OfflineCache
            cache_path = os.path.join(
                os.path.dirname(ConfigManager.path()), "offline_cache.jsonl"
            )
            cache = OfflineCache(cache_path, self.config.offline_cache_max)
            cache.append(envelope)
            _log(f"Cached offline ({cache.size()} total)")

        return False

    def post_and_wait(self, envelope: dict, timeout: int) -> dict | None:
        """POST 审批请求，同步等待 relay HTTP 响应（阻塞）"""
        url = f"{self.config.relay_url}/hook/relay"
        headers = {"Authorization": f"Bearer {self.config.jwt}"}

        try:
            resp = self.client.post(
                url, json=envelope, headers=headers,
                timeout=httpx.Timeout(timeout / 1000.0 + 5),
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401 and self._try_refresh():
                # 刷新成功，重试一次
                headers["Authorization"] = f"Bearer {self.config.jwt}"
                resp = self.client.post(
                    url, json=envelope, headers=headers,
                    timeout=httpx.Timeout(timeout / 1000.0 + 5),
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            _log(f"Approval request failed: {e}")
        return None  # 返回 None → 触发 fallback

    def _try_refresh(self) -> bool:
        """JWT 刷新 — 并发安全。刷新前先重读 config.json，
        因为可能已被其他 Hook 进程刷新过。"""
        if not self.config.refresh_token:
            return False

        # 重读 config，可能已被其他进程更新
        fresh = ConfigManager.load()
        if fresh.jwt != self.config.jwt:
            # 其他进程已刷新，直接用新的
            self.config["jwt"] = fresh.jwt
            self.config["refresh_token"] = fresh.refresh_token
            return True

        # 需要自己刷新
        try:
            resp = self.client.post(
                f"{self.config.relay_url}/auth/refresh",
                json={"refresh_token": self.config.refresh_token},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.config["jwt"] = data["jwt"]
                self.config["refresh_token"] = data["refresh_token"]
                ConfigManager.save(self.config)
                return True
        except Exception:
            pass

        # 刷新失败（refresh_token 也过期了，极端情况）
        _log("JWT refresh failed — refresh_token expired, please re-run /cli-notify:setup")
        return False


def _log(msg: str):
    print(f"[cli-notify] {msg}", file=sys.stderr)
```

## 2. hooks.json 设计

18 核心 Hook 全部启用，timeout 根据 Hook 语义设置：

| Hook | Timeout | 原因 |
|------|---------|------|
| SessionStart | 15000ms | 可能涉及网络请求 |
| SessionEnd | 10000ms | 实际限制 1.5s，但不设太短 |
| UserPromptSubmit | 10000ms | 快速发送 |
| PreToolUse | 30000ms | 可能等待手机审批 |
| PostToolUse | 10000ms | 快速发送 |
| PostToolUseFailure | 10000ms | 快速发送 |
| PostToolBatch | 10000ms | 快速发送 |
| PermissionRequest | 30000ms | 等待手机审批 |
| PermissionDenied | 10000ms | 快速发送 |
| Stop | 15000ms | 可能发送 idle 通知 |
| StopFailure | 10000ms | 快速发送 |
| Notification | 10000ms | 快速发送 |
| MessageDisplay | 10000ms | 高频事件，合并后发送 |
| SubagentStart | 10000ms | 快速发送 |
| SubagentStop | 15000ms | 可能包含总结 |
| TaskCreated | 10000ms | 快速发送 |
| TaskCompleted | 10000ms | 快速发送 |
| Elicitation | 30000ms | 等待用户输入 |

## 3. setup.md 交互式配置流程

```
/cli-notify:setup <relay-url> <pairing-key>

Step 1: 认证
  POST {relay-url}/auth/login { user_id: "desktop", secret: pairing_key }
  → 获取 jwt + refresh_token

Step 2: 审批模式
  选择: [A] 桌面审批  [B] 手机审批  [C] 混合(手机优先,超时回桌面)
  默认: A

Step 3: 审批超时 (需 B/C)
  输入毫秒数 (10000-120000)
  默认: 30000

Step 4: 超时策略 (需 B/C)
  选择: [A] 自动拒绝  [B] 自动允许  [C] 交回桌面
  默认: C

Step 5: 数据截断上限
  输入字节数 (10240-1048576)
  默认: 51200

Step 6: 离线缓存
  选择: [A] 不缓存(静默丢弃)  [B] 本地缓存(重连后重发)
  默认: A

Step 7: 缓存上限 (需 B)
  输入条数 (100-10000)
  默认: 1000

Step 8: 扩展 Hook
  勾选启用的扩展 Hook (12 个可选):
  [ ] UserPromptExpansion  [ ] Setup
  [ ] PreCompact           [ ] PostCompact
  [ ] TeammateIdle         [ ] ConfigChange
  [ ] CwdChanged           [ ] FileChanged
  [ ] InstructionsLoaded   [ ] WorktreeCreate
  [ ] WorktreeRemove       [ ] ElicitationResult

Step 9: 确认 & 保存
  显示配置摘要 → 写入 .cli-notify/config.json
  → 生成 hooks.json → 验证连通性 /health
```

## 4. 测试设计

### 测试文件: `tests/plugin/test_relay_forward.py`

**新增测试用例 (约 60 个):**

| 类别 | 测试数 | 内容 |
|------|--------|------|
| Hook 映射 | 32 | 30 个已知 + 1 个未知 Hook → 正确 EventType; 1 个空 Hook 名 |
| 启用检查 | 6 | 核心 18 默认启用; 扩展需 extra_hooks; 未启用返回 None |
| msgType 判定 | 6 | desktop 模式全部 event; app 模式 PreToolUse/PermissionRequest/Elicitation 为 request |
| tool_use_id | 4 | 有 tool_use_id 正确提取; 无则为 None |
| agent_id | 4 | Subagent 事件有 agent_id; 主 agent 事件无 |
| turn_id | 6 | UserPromptSubmit 生成新 turn_id; Stop 清除; 中间事件复用 |
| 数据截断 | 5 | 不超限不截断; 超限 truncated=true; _original_size 记录 |
| MessageDisplay 合并 | 6 | 同 message_id 窗口内合并; 新 message_id 立即发送前序; flush 行为 |
| 二进制兜底 | 2 | 不可序列化对象 → _raw_b64 |
| E2EE 加密 | 5 | encrypted=true; EncryptedPayload 格式; info string 为 v2 |
| Envelope 结构 | 6 | 必填字段完整; correlationId 匹配 tool_use_id; groupId 匹配 agent_id |
| 决策响应 | 8 | PreToolUse allow/deny/ask/超时; PermissionRequest; Elicitation |
| 重试逻辑 | 3 | 成功不重试; 5xx 重试; 非可重试码不重试 |
| 配置加载 | 3 | 默认配置; 覆盖配置; 文件不存在 |
