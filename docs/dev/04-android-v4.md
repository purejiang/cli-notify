# Phase 4: Android App v4 开发详细设计

> 依赖：Phase 1 Protocol v2 + Phase 3 Relay v4 完成
> 技术栈：Kotlin + Jetpack Compose + Material Design 3

## 1. 数据层

### 1.1 Protocol.kt — 完全重写

```kotlin
package com.clinotify.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class Envelope(
    val type: String,
    val id: String,
    val msgType: String,
    val sessionId: String,
    val from: String,
    val timestamp: Long,
    val encrypted: Boolean,
    val data: JsonObject,
    val correlationId: String? = null,
    val groupId: String? = null,
    // 审批响应专用（中继层元数据，非业务数据）
    @SerialName("_decision")
    val approvalDecision: ApprovalDecision? = null,
)

@Serializable
data class EnvelopeData(
    val raw: JsonObject,
    @SerialName("tool_use_id") val toolUseId: String? = null,
    @SerialName("agent_id") val agentId: String? = null,
    @SerialName("turn_id") val turnId: String? = null,
    val truncated: Boolean = false,
)

@Serializable
data class EncryptedPayload(
    val ephemeralKey: String,
    val iv: String,
    val ciphertext: String,
)

@Serializable
data class ApprovalDecision(
    val decision: String,
    val reason: String = "",
)

@Serializable
data class UserPreferences(
    @SerialName("approval_timeout_ms") val approvalTimeoutMs: Int = 30000,
    @SerialName("fallback_action") val fallbackAction: String = "ask",
)
```

### 1.2 EventParser.kt

```kotlin
class EventParser(private val cryptoManager: CryptoManager?) {

    fun parse(rawJson: String): Envelope? {
        return try {
            val root = Json.parseToJsonElement(rawJson).jsonObject
            val encrypted = root["encrypted"]?.jsonPrimitive?.boolean ?: false

            if (encrypted && cryptoManager != null) {
                val encPayload = Json.decodeFromJsonElement<EncryptedPayload>(root["data"]!!)
                val plainData = cryptoManager.decrypt(encPayload)
                val mutable = root.toMutableMap()
                mutable["data"] = plainData
                mutable["encrypted"] = JsonPrimitive(false)
                Json.decodeFromJsonElement<Envelope>(JsonObject(mutable))
            } else {
                Json.decodeFromJsonElement<Envelope>(root)
            }
        } catch (e: Exception) {
            Log.e("EventParser", "Parse error", e)
            null
        }
    }
}
```

### 1.3 EventBridge.kt — 30 种 EventType 全覆盖

```kotlin
object EventBridge {

    fun toNotification(envelope: Envelope): NotificationMessage? {
        val data = extractData(envelope)
        val raw = data.raw
        val type = envelope.type

        return when (type) {
            // === 会话 ===
            "session_start" -> {
                val cwd = raw.string("cwd") ?: ""
                val source = raw.string("source") ?: "startup"
                NotificationMessage(
                    type = type,
                    title = if (source == "resume") "会话恢复" else "会话开始",
                    body = cwd,
                    sessionId = envelope.sessionId,
                    cwd = cwd,
                )
            }
            "session_end" -> {
                NotificationMessage(
                    type = type,
                    title = "会话结束",
                    body = raw.string("reason") ?: "",
                    sessionId = envelope.sessionId,
                )
            }

            // === 对话 ===
            "user_prompt_submit" -> {
                NotificationMessage(
                    type = type,
                    title = "新提问",
                    body = raw.string("prompt")?.take(200) ?: "",
                    sessionId = envelope.sessionId,
                    turnId = data.turnId,
                )
            }
            "stop" -> {
                NotificationMessage(
                    type = type,
                    title = "响应完成",
                    body = raw.string("last_assistant_message")?.take(200) ?: "",
                    sessionId = envelope.sessionId,
                )
            }
            "stop_failure" -> {
                NotificationMessage(
                    type = type,
                    title = "⚠️ API 错误",
                    body = "${raw.string("error") ?: ""}: ${raw.string("error_details") ?: ""}",
                    sessionId = envelope.sessionId,
                    priority = NotificationPriority.HIGH,
                )
            }

            // === 工具 ===
            "pre_tool_use" -> {
                val toolName = raw.string("tool_name") ?: ""
                NotificationMessage(
                    type = type,
                    title = "🔧 $toolName",
                    body = toolSummary(toolName, raw.jsonObject("tool_input")),
                    sessionId = envelope.sessionId,
                    toolUseId = data.toolUseId,
                    agentId = data.agentId,
                    status = MessageStatus.RUNNING,
                )
            }
            "post_tool_use" -> {
                val toolName = raw.string("tool_name") ?: ""
                val success = raw.jsonObject("tool_response")?.string("success")?.toBooleanStrictOrNull() ?: true
                NotificationMessage(
                    type = type,
                    title = if (success) "✅ $toolName" else "⚠️ $toolName",
                    body = "${raw.long("duration_ms") ?: 0}ms",
                    sessionId = envelope.sessionId,
                    toolUseId = data.toolUseId,
                    status = if (success) MessageStatus.DONE else MessageStatus.FAILED,
                )
            }
            "post_tool_use_failure" -> {
                NotificationMessage(
                    type = type,
                    title = "❌ ${raw.string("tool_name") ?: ""}",
                    body = raw.string("error")?.take(200) ?: "",
                    sessionId = envelope.sessionId,
                    toolUseId = data.toolUseId,
                    status = MessageStatus.FAILED,
                )
            }
            "post_tool_batch" -> {
                NotificationMessage(
                    type = type,
                    title = "📦 批次完成",
                    body = "工具批次执行完成",
                    sessionId = envelope.sessionId,
                )
            }

            // === 权限 ===
            "permission_request" -> {
                if (envelope.msgType == "request") {
                    val toolName = raw.string("tool_name") ?: ""
                    NotificationMessage(
                        type = type,
                        title = "🔐 需要审批: $toolName",
                        body = toolSummary(toolName, raw.jsonObject("tool_input")),
                        sessionId = envelope.sessionId,
                        toolUseId = data.toolUseId,
                        correlationId = envelope.correlationId,
                        status = MessageStatus.WAITING_APPROVAL,
                        priority = NotificationPriority.HIGH,
                    )
                } else null
            }
            "permission_denied" -> {
                NotificationMessage(
                    type = type,
                    title = "🚫 权限拒绝",
                    body = "${raw.string("tool_name") ?: ""}: ${raw.string("reason") ?: ""}",
                    sessionId = envelope.sessionId,
                    toolUseId = data.toolUseId,
                )
            }

            // === 通知 ===
            "notification" -> {
                val msg = raw.string("message") ?: ""
                val nType = raw.string("notification_type") ?: ""
                NotificationMessage(
                    type = type,
                    title = when (nType) {
                        "permission_prompt" -> "需要权限"
                        "idle_prompt" -> "等待输入"
                        "elicitation_dialog" -> "需要输入"
                        else -> "通知"
                    },
                    body = msg.take(200),
                    sessionId = envelope.sessionId,
                )
            }

            // === 流式消息 ===
            "message_display" -> {
                NotificationMessage(
                    type = type,
                    title = "回复中...",
                    body = raw.string("delta") ?: "",
                    sessionId = envelope.sessionId,
                    messageId = raw.string("message_id"),
                    isStreaming = raw.boolean("final") != true,
                )
            }

            // === Agent ===
            "subagent_start" -> {
                NotificationMessage(
                    type = type,
                    title = "🤖 子代理启动",
                    body = raw.string("agent_type") ?: "",
                    sessionId = envelope.sessionId,
                    agentId = data.agentId,
                    status = MessageStatus.RUNNING,
                )
            }
            "subagent_stop" -> {
                NotificationMessage(
                    type = type,
                    title = "🤖 子代理完成",
                    body = raw.string("last_assistant_message")?.take(200) ?: "",
                    sessionId = envelope.sessionId,
                    agentId = data.agentId,
                    status = MessageStatus.DONE,
                )
            }

            // === Task ===
            "task_created" -> {
                NotificationMessage(
                    type = type,
                    title = "📋 任务: ${raw.string("task_subject") ?: ""}",
                    body = raw.string("teammate_name") ?: "",
                    sessionId = envelope.sessionId,
                )
            }
            "task_completed" -> {
                NotificationMessage(
                    type = type,
                    title = "✅ 任务完成",
                    body = raw.string("task_subject") ?: "",
                    sessionId = envelope.sessionId,
                )
            }

            // === Elicitation ===
            "elicitation" -> {
                if (envelope.msgType == "request") {
                    NotificationMessage(
                        type = type,
                        title = "需要输入 (${raw.string("mcp_server_name") ?: ""})",
                        body = raw.string("message")?.take(200) ?: "",
                        sessionId = envelope.sessionId,
                        correlationId = envelope.correlationId,
                        status = MessageStatus.WAITING_APPROVAL,
                    )
                } else null
            }

            // === 扩展（通用处理） ===
            "user_prompt_expansion", "setup", "pre_compact", "post_compact",
            "teammate_idle", "config_change", "cwd_changed", "file_changed",
            "instructions_loaded", "worktree_create", "worktree_remove",
            "elicitation_result" -> {
                NotificationMessage(
                    type = type,
                    title = type.replace("_", " "),
                    body = raw.toString().take(200),
                    sessionId = envelope.sessionId,
                )
            }

            // === 系统 ===
            "auth_success" -> null
            "error" -> {
                NotificationMessage(
                    type = type,
                    title = "错误: ${raw.string("code") ?: ""}",
                    body = raw.string("message") ?: "",
                    sessionId = envelope.sessionId,
                    priority = NotificationPriority.HIGH,
                )
            }

            // === 兜底 ===
            else -> {
                NotificationMessage(
                    type = type,
                    title = type.replace("_", " "),
                    body = raw.toString().take(200),
                    sessionId = envelope.sessionId,
                )
            }
        }
    }

    private fun extractData(envelope: Envelope): EnvelopeData {
        return try {
            Json.decodeFromJsonElement<EnvelopeData>(envelope.data)
        } catch (e: Exception) {
            EnvelopeData(raw = envelope.data["raw"]?.jsonObject ?: JsonObject(emptyMap()))
        }
    }

    private fun toolSummary(toolName: String, input: JsonObject?): String {
        if (input == null) return ""
        return when (toolName) {
            "Bash" -> input.string("command")?.take(100) ?: ""
            "Write", "Edit" -> input.string("file_path")?.let { "→ $it" } ?: ""
            "Read" -> input.string("file_path")?.let { "📖 $it" } ?: ""
            "Glob" -> input.string("pattern") ?: ""
            "Grep" -> input.string("pattern")?.take(80) ?: ""
            "WebFetch" -> input.string("url")?.take(80) ?: ""
            "WebSearch" -> input.string("query")?.take(80) ?: ""
            "Agent" -> input.string("description")?.take(100) ?: ""
            "AskUserQuestion" -> "向用户提问"
            else -> input.toString().take(100)
        }
    }
}

// 扩展函数
fun JsonObject.string(key: String): String? = this[key]?.jsonPrimitive?.content
fun JsonObject.boolean(key: String): Boolean? = this[key]?.jsonPrimitive?.booleanOrNull
fun JsonObject.long(key: String): Long? = this[key]?.jsonPrimitive?.longOrNull
fun JsonObject.jsonObject(key: String): JsonObject? = this[key]?.jsonObject

enum class MessageStatus { RUNNING, DONE, FAILED, WAITING_APPROVAL }
enum class NotificationPriority { NORMAL, HIGH }

data class NotificationMessage(
    val type: String,
    val title: String,
    val body: String,
    val sessionId: String,
    val cwd: String? = null,
    val turnId: String? = null,
    val toolUseId: String? = null,
    val agentId: String? = null,
    val messageId: String? = null,
    val correlationId: String? = null,
    val status: MessageStatus? = null,
    val priority: NotificationPriority = NotificationPriority.NORMAL,
    val isStreaming: Boolean = false,
    val truncated: Boolean = false,
    val timestamp: Long = System.currentTimeMillis(),
)
```

## 2. CryptoManager.kt — 更新 info string

```kotlin
companion object {
    private const val HKDF_INFO = "cli-notify-v2"
    // P-256 密钥生成、AES-GCM 解密、HKDF 派生逻辑保持不变
}
```

## 3. NotificationStore — 状态管理增强

```kotlin
class NotificationStore(private val context: Context) {
    private val _messages = MutableStateFlow<List<NotificationMessage>>(emptyList())
    val messages: StateFlow<List<NotificationMessage>> = _messages.asStateFlow()

    fun onEnvelopeReceived(envelope: Envelope) {
        val msg = EventBridge.toNotification(envelope) ?: return

        when {
            // 流式消息：追加 delta
            msg.isStreaming && msg.messageId != null -> appendDelta(msg)
            // 工具结果：更新 PreToolUse 卡片
            msg.toolUseId != null && msg.type in listOf("post_tool_use", "post_tool_use_failure") ->
                updateToolStatus(msg.toolUseId, msg.status!!)
            // 审批响应：更新对应卡片
            envelope.msgType == "response" && envelope.correlationId != null ->
                updateApprovalStatus(envelope.correlationId, envelope.approvalDecision)
            // 新消息
            else -> _messages.value = _messages.value + msg
        }
        persist()
    }

    private fun updateToolStatus(toolUseId: String, status: MessageStatus) {
        _messages.value = _messages.value.map {
            if (it.toolUseId == toolUseId) it.copy(status = status) else it
        }
    }

    private fun updateApprovalStatus(correlationId: String, decision: ApprovalDecision?) {
        val newStatus = if (decision?.decision == "allow") MessageStatus.DONE else MessageStatus.FAILED
        _messages.value = _messages.value.map {
            if (it.correlationId == correlationId)
                it.copy(status = newStatus, body = decision?.reason ?: it.body)
            else it
        }
    }

    private fun appendDelta(streamMsg: NotificationMessage) {
        _messages.value = _messages.value.map {
            if (it.messageId == streamMsg.messageId && it.isStreaming)
                it.copy(body = it.body + streamMsg.body, isStreaming = streamMsg.isStreaming)
            else it
        }.let { updated ->
            if (updated.none { it.messageId == streamMsg.messageId })
                updated + streamMsg
            else updated
        }
    }

    fun getPendingApprovals() =
        _messages.value.filter { it.status == MessageStatus.WAITING_APPROVAL }
}
```

## 4. ConnectionManager — 审批发送 + 完整内容请求

```kotlin
class ConnectionManager(
    private val settingsStore: SettingsStore,
    private val cryptoManager: CryptoManager,
) {
    // 审批超时本地计时器
    private val approvalTimers = ConcurrentHashMap<String, Job>()

    suspend fun sendApproval(correlationId: String, decision: String, reason: String) {
        // 构建响应 envelope，_decision 不加密放在顶层
        sendJson(buildJsonObject {
            put("type", JsonPrimitive("permission_request"))
            put("msgType", JsonPrimitive("response"))
            put("from", JsonPrimitive("mobile"))
            put("correlationId", JsonPrimitive(correlationId))
            put("encrypted", JsonPrimitive(false))
            put("data", buildJsonObject {
                put("raw", buildJsonObject {
                    put("ack", JsonPrimitive(true))
                })
                put("truncated", JsonPrimitive(false))
            })
            put("_decision", buildJsonObject {
                put("decision", JsonPrimitive(decision))
                put("reason", JsonPrimitive(reason))
            })
        }.toString())
        cancelApprovalTimer(correlationId)
    }

    fun startApprovalTimer(correlationId: String, timeoutMs: Int) {
        approvalTimers[correlationId] = scope.launch {
            delay(timeoutMs.toLong())
            _approvalEvents.emit(ApprovalEvent.Timeout(correlationId))
        }
    }

    fun cancelApprovalTimer(correlationId: String) {
        approvalTimers.remove(correlationId)?.cancel()
    }

    suspend fun requestFullContent(targetId: String) {
        sendJson(buildJsonObject {
            put("type", JsonPrimitive("sync"))
            put("msgType", JsonPrimitive("request"))
            put("from", JsonPrimitive("mobile"))
            put("correlationId", JsonPrimitive(UUID.randomUUID().toString()))
            put("encrypted", JsonPrimitive(false))
            put("data", buildJsonObject {
                put("raw", buildJsonObject {
                    put("query", JsonPrimitive("full_message"))
                    put("params", buildJsonObject {
                        put("target_id", JsonPrimitive(targetId))
                    })
                })
                put("truncated", JsonPrimitive(false))
            })
        }.toString())
    }
}
```

## 5. UI 层

### 5.1 主列表 — LazyColumn + 分组

```kotlin
@Composable
fun MessageList(
    messages: List<NotificationMessage>,
    onRequestFullContent: (String) -> Unit,
) {
    val grouped = remember(messages) {
        messages.groupBy { it.sessionId }.mapValues { (_, msgs) ->
            msgs.groupBy { it.turnId ?: it.agentId ?: "default" }
        }
    }

    LazyColumn {
        grouped.forEach { (sessionId, groups) ->
            item(key = "session-$sessionId") {
                Text("Session: $sessionId", style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
            }
            groups.forEach { (_, msgs) ->
                itemsIndexed(msgs,
                    key = { _, m -> m.toolUseId ?: m.correlationId ?: m.messageId ?: m.hashCode().toString() }
                ) { _, msg ->
                    MessageCard(msg, onRequestFullContent)
                }
            }
        }
    }
}
```

### 5.2 消息卡片

```kotlin
@Composable
fun MessageCard(msg: NotificationMessage, onRequestFullContent: (String) -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 3.dp)
            .clickable {
                if (msg.truncated)
                    onRequestFullContent(msg.correlationId ?: msg.toolUseId ?: "")
            },
        colors = CardDefaults.cardColors(
            containerColor = when (msg.status) {
                MessageStatus.WAITING_APPROVAL -> MaterialTheme.colorScheme.tertiaryContainer
                MessageStatus.FAILED -> MaterialTheme.colorScheme.errorContainer
                MessageStatus.RUNNING -> MaterialTheme.colorScheme.secondaryContainer
                else -> MaterialTheme.colorScheme.surfaceVariant
            }
        ),
    ) {
        Row(modifier = Modifier.padding(10.dp), verticalAlignment = Alignment.Top) {
            // 状态图标
            when {
                msg.isStreaming -> CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                msg.status == MessageStatus.RUNNING -> Icon(Icons.Default.MoreHoriz, null, Modifier.size(16.dp))
                msg.status == MessageStatus.DONE -> Icon(Icons.Default.Check, null, Modifier.size(16.dp), tint = Color(0xFF4CAF50))
                msg.status == MessageStatus.FAILED -> Icon(Icons.Default.Close, null, Modifier.size(16.dp), tint = Color(0xFFF44336))
                msg.status == MessageStatus.WAITING_APPROVAL -> Icon(Icons.Default.HourglassEmpty, null, Modifier.size(16.dp), tint = Color(0xFFFF9800))
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(msg.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                if (msg.body.isNotEmpty())
                    Text(msg.body, style = MaterialTheme.typography.bodySmall,
                        maxLines = if (msg.truncated) 3 else 5,
                        overflow = TextOverflow.Ellipsis)
                if (msg.truncated)
                    Text("📎 已截断，点击查看完整",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}
```

### 5.3 审批对话框

```kotlin
@Composable
fun ApprovalDialog(
    request: NotificationMessage,
    remainingMs: Long,
    onApprove: (String) -> Unit,
    onDeny: (String) -> Unit,
) {
    AlertDialog(
        onDismissRequest = {},
        title = { Text("权限请求") },
        text = {
            Column {
                Text(request.title, fontWeight = FontWeight.Bold)
                Text(request.body)
                Spacer(Modifier.height(8.dp))
                Surface(color = if (remainingMs < 10000) Color(0xFFFFEBEE) else MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(8.dp)) {
                    Text("⏱ ${remainingMs / 1000}s",
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                        color = if (remainingMs < 10000) Color(0xFFD32F2F) else MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        },
        confirmButton = { Button(onClick = { onApprove(request.correlationId!!) }) { Text("允许") } },
        dismissButton = { OutlinedButton(onClick = { onDeny(request.correlationId!!) }) { Text("拒绝") } },
    )
}
```

## 6. 产出物清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `data/model/Protocol.kt` | 重写 | v2 数据类，增加 `_decision` 字段 |
| `data/ws/EventParser.kt` | 更新 | v2 解密 + extractData |
| `data/EventBridge.kt` | 重写 | 30 种 EventType 全覆盖 + 兜底 |
| `data/NotificationStore.kt` | 更新 | 工具状态更新、流式追加、审批更新 |
| `data/ConnectionManager.kt` | 更新 | sendApproval（_decision 顶层）、requestFullContent、超时计时 |
| `data/crypto/CryptoManager.kt` | 更新 | HKDF info="cli-notify-v2" |
| `ui/MainScreen.kt` | 重写 | LazyColumn + 分组 |
| `ui/ApprovalDialog.kt` | 新建 | 审批对话框 + 倒计时 |
