/**
 * Plugin Integration Tests
 *
 * Simulates Claude Code hook stdin inputs and validates:
 *   1. Data extraction (extractData) for each hook event type
 *   2. Envelope format compliance with protocol schema
 *   3. Enrichment logic (Edit line numbers, idle notification)
 *   4. Hook event type mapping (HOOK_EVENT_MAP)
 *   5. Encryption payload format (encryptPayload)
 *
 * Since the plugin's index.ts reads stdin and uses fetch (not mockable easily),
 * we test the pure functions directly: extractData, buildIdleNotification,
 * encryptPayload, and the type mappings.
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, "..", "..");

// =========================================================================
// Mini test runner
// =========================================================================

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  PASS: ${name}`);
  } catch (e) {
    failed++;
    console.log(`  FAIL: ${name}`);
    console.log(`        ${e.message}`);
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message ?? "assertion failed");
}

// =========================================================================
// Mock HookInputs for each Claude Code hook event
// =========================================================================

const MOCK_HOOKS = {
  SessionStart: {
    hook_event_name: "SessionStart",
    session_id: "sess-test-001",
    cwd: "/home/user/myproject",
  },
  UserPromptSubmit: {
    hook_event_name: "UserPromptSubmit",
    session_id: "sess-test-001",
    prompt: "请帮我写一个排序函数",
  },
  PreToolUse: {
    hook_event_name: "PreToolUse",
    session_id: "sess-test-001",
    tool_name: "Bash",
    tool_input: { command: "ls -la", description: "List files" },
  },
  PostToolUse: {
    hook_event_name: "PostToolUse",
    session_id: "sess-test-001",
    tool_name: "Bash",
    tool_response: { stdout: "file1.txt\nfile2.txt", stderr: "", exitCode: 0 },
  },
  PostToolUse_Edit: {
    hook_event_name: "PostToolUse",
    session_id: "sess-test-001",
    tool_name: "Edit",
    tool_response: {
      oldString: "hello world",
      newString: "hello universe",
      originalFile: "line 1\nline 2\nhello world\nline 4\n",
      replaceAll: false,
    },
  },
  PermissionRequest: {
    hook_event_name: "PermissionRequest",
    session_id: "sess-test-001",
    tool_name: "Bash",
    tool_input: { command: "rm -rf /tmp/test" },
  },
  Stop: {
    hook_event_name: "Stop",
    session_id: "sess-test-001",
    cwd: "/home/user/myproject",
    last_assistant_message: "已完成！以下是排序后的结果：\n[1, 2, 3, 4, 5]",
  },
  SessionEnd: {
    hook_event_name: "SessionEnd",
    session_id: "sess-test-001",
    reason: "user closed terminal",
  },
  Notification: {
    hook_event_name: "Notification",
    session_id: "sess-test-001",
    notification_type: "permission_prompt",
    message: "Claude wants to run a command",
  },
};

// =========================================================================
// Import plugin modules
// Since plugin uses ESM, we can import directly
// =========================================================================

import { extractData, buildIdleNotification } from "../../cli-notify-plugin/src/enrich.js";
import { HOOK_EVENT_MAP, VALID_NOTIFICATION_KINDS } from "../../cli-notify-plugin/src/types.js";
import { encryptPayload } from "../../cli-notify-plugin/src/crypto.js";

// =========================================================================
// Tests: Data Extraction (extractData)
// =========================================================================

console.log("\n=== Plugin Data Extraction Tests ===\n");

test("extractData: SessionStart → { cwd }", () => {
  const data = extractData(MOCK_HOOKS.SessionStart);
  assert(data.cwd === "/home/user/myproject", `Expected /home/user/myproject, got ${data.cwd}`);
});

test("extractData: SessionStart with missing cwd → empty string", () => {
  const data = extractData({ ...MOCK_HOOKS.SessionStart, cwd: undefined });
  assert(data.cwd === "", `Expected empty string, got ${data.cwd}`);
});

test("extractData: UserPromptSubmit → { content }", () => {
  const data = extractData(MOCK_HOOKS.UserPromptSubmit);
  assert(data.content === "请帮我写一个排序函数", `Expected prompt, got ${data.content}`);
});

test("extractData: UserPromptSubmit with missing prompt → empty string", () => {
  const data = extractData({ ...MOCK_HOOKS.UserPromptSubmit, prompt: undefined });
  assert(data.content === "", `Expected empty string, got ${data.content}`);
});

test("extractData: PreToolUse → { toolName, params }", () => {
  const data = extractData(MOCK_HOOKS.PreToolUse);
  assert(data.toolName === "Bash", `Expected Bash, got ${data.toolName}`);
  assert(data.params.command === "ls -la", `Expected ls -la, got ${data.params.command}`);
});

test("extractData: PostToolUse → { toolName, output, success }", () => {
  const data = extractData(MOCK_HOOKS.PostToolUse);
  assert(data.toolName === "Bash");
  assert(data.success === true);
  assert(data.output !== null, "output should not be null");
  assert(typeof data.output === "string", "output should be a string");
});

test("extractData: PostToolUse Edit → enriched with editLineInfo", () => {
  const data = extractData(MOCK_HOOKS.PostToolUse_Edit);
  assert(data.toolName === "Edit");
  assert(data.editLineInfo !== undefined, "Edit tool should have editLineInfo");
  assert(data.editLineInfo.oldLineStart === 3, `Expected line 3, got ${data.editLineInfo.oldLineStart}`);
  assert(data.editLineInfo.oldLineEnd === 3, `Expected line 3, got ${data.editLineInfo.oldLineEnd}`);
  assert(data.editLineInfo.replaceAll === false);
});

test("extractData: PermissionRequest → { toolName, params, message }", () => {
  const data = extractData(MOCK_HOOKS.PermissionRequest);
  assert(data.toolName === "Bash");
  assert(typeof data.message === "string", "message should be a string");
  assert(data.message.length > 0, "message should not be empty");
});

test("extractData: Stop → { content, model, tokens, stopReason }", () => {
  const data = extractData(MOCK_HOOKS.Stop);
  assert(data.content === "已完成！以下是排序后的结果：\n[1, 2, 3, 4, 5]");
  assert(data.model === "");
  assert(data.tokens !== undefined);
  assert(data.tokens.input === 0);
  assert(data.tokens.output === 0);
  assert(data.stopReason === "");
});

test("extractData: Stop with whitespace-only content → trimmed empty", () => {
  const data = extractData({ ...MOCK_HOOKS.Stop, last_assistant_message: "   \n  " });
  assert(data.content === "", `Expected empty, got "${data.content}"`);
});

test("extractData: SessionEnd → { reason }", () => {
  const data = extractData(MOCK_HOOKS.SessionEnd);
  assert(data.reason === "user closed terminal");
});

test("extractData: Notification → { kind, message, cwd }", () => {
  const data = extractData(MOCK_HOOKS.Notification);
  assert(data.kind === "permission_prompt");
  assert(data.message === "Claude wants to run a command");
});

test("extractData: Notification with invalid kind → defaults to idle_prompt", () => {
  const data = extractData({
    ...MOCK_HOOKS.Notification,
    notification_type: "invalid_kind",
  });
  assert(data.kind === "idle_prompt", `Expected idle_prompt, got ${data.kind}`);
});

test("extractData: unknown hook event → empty object", () => {
  const data = extractData({ hook_event_name: "UnknownHook", session_id: "x" });
  assert(Object.keys(data).length === 0, `Expected empty, got ${JSON.stringify(data)}`);
});

// =========================================================================
// Tests: Enrichment Helpers
// =========================================================================

console.log("\n--- Enrichment: Edit Line Numbers ---");

test("computeEditLineNumbers: finds oldString at correct line", () => {
  const data = extractData({
    hook_event_name: "PostToolUse",
    session_id: "sess-1",
    tool_name: "Edit",
    tool_response: {
      oldString: "const x = 1;\nconst y = 2;",
      newString: "const z = 3;",
      originalFile: "// header\n// comment\nconst x = 1;\nconst y = 2;\n// footer\n",
      replaceAll: true,
    },
  });
  assert(data.editLineInfo.oldLineStart === 3, `Expected line 3, got ${data.editLineInfo.oldLineStart}`);
  assert(data.editLineInfo.oldLineEnd === 4, `Expected line 4, got ${data.editLineInfo.oldLineEnd}`);
  assert(data.editLineInfo.replaceAll === true);
});

test("computeEditLineNumbers: returns null when oldString not found", () => {
  const data = extractData({
    hook_event_name: "PostToolUse",
    session_id: "sess-1",
    tool_name: "Edit",
    tool_response: {
      oldString: "nonexistent",
      newString: "replacement",
      originalFile: "line 1\nline 2\n",
      replaceAll: false,
    },
  });
  assert(data.editLineInfo === undefined, "editLineInfo should be undefined when oldString not found");
});

test("computeEditLineNumbers: returns null when fields missing", () => {
  const data = extractData({
    hook_event_name: "PostToolUse",
    session_id: "sess-1",
    tool_name: "Edit",
    tool_response: { newString: "replacement" },
  });
  assert(data.editLineInfo === undefined, "editLineInfo should be undefined when originalFile missing");
});

// =========================================================================
// Tests: Idle Notification Builder
// =========================================================================

console.log("\n--- Idle Notification Builder ---");

test("buildIdleNotification: produces correct structure", () => {
  const notification = buildIdleNotification("/home/user/project");
  assert(notification.kind === "idle_prompt");
  assert(notification.message === null);
  assert(notification.cwd === "/home/user/project");
});

test("buildIdleNotification: handles empty cwd", () => {
  const notification = buildIdleNotification("");
  assert(notification.cwd === "");
});

// =========================================================================
// Tests: Hook Event Mapping
// =========================================================================

console.log("\n--- Hook Event Mapping ---");

const EXPECTED_MAPPINGS = {
  SessionStart: "session.start",
  UserPromptSubmit: "message.user",
  PreToolUse: "tool.request",
  PostToolUse: "tool.result",
  PermissionRequest: "tool.permission_request",
  Stop: "message.assistant",
  SessionEnd: "session.end",
  Notification: "notification",
};

for (const [hookName, eventType] of Object.entries(EXPECTED_MAPPINGS)) {
  test(`HOOK_EVENT_MAP["${hookName}"] → "${eventType}"`, () => {
    assert(HOOK_EVENT_MAP[hookName] === eventType,
      `Expected "${eventType}", got "${HOOK_EVENT_MAP[hookName]}"`);
  });
}

test("HOOK_EVENT_MAP: all 8 hooks are mapped", () => {
  assert(Object.keys(HOOK_EVENT_MAP).length === 8,
    `Expected 8, got ${Object.keys(HOOK_EVENT_MAP).length}`);
});

// =========================================================================
// Tests: Notification Kind Validation
// =========================================================================

console.log("\n--- Notification Kind Validation ---");

test("VALID_NOTIFICATION_KINDS: contains permission_prompt, idle_prompt, auth_success", () => {
  assert(VALID_NOTIFICATION_KINDS.has("permission_prompt"));
  assert(VALID_NOTIFICATION_KINDS.has("idle_prompt"));
  assert(VALID_NOTIFICATION_KINDS.has("auth_success"));
  assert(!VALID_NOTIFICATION_KINDS.has("invalid"));
});

// =========================================================================
// Tests: E2EE Encryption Format
// =========================================================================

console.log("\n--- E2EE Encryption ---");

// Generate a valid P-256 key for testing
import { createECDH } from "node:crypto";
const TEST_KEY = (() => {
  const ecdh = createECDH("prime256v1");
  return ecdh.generateKeys().toString("base64");
})();

test("encryptPayload: produces valid EncryptedPayload shape", () => {
  const data = { cwd: "/test", content: "hello" };
  const payload = encryptPayload(data, TEST_KEY);

  // Check required fields
  assert(typeof payload.ephemeralKey === "string", "ephemeralKey must be a string");
  assert(typeof payload.iv === "string", "iv must be a string");
  assert(typeof payload.ciphertext === "string", "ciphertext must be a string");

  // Check base64 format
  assert(payload.ephemeralKey.length > 0, "ephemeralKey must not be empty");
  assert(payload.iv.length > 0, "iv must not be empty");
  assert(payload.ciphertext.length > 0, "ciphertext must not be empty");

  // Ephemeral key should be 65 bytes (uncompressed P-256 point)
  const keyBuf = Buffer.from(payload.ephemeralKey, "base64");
  assert(keyBuf.length === 65, `Expected 65 bytes, got ${keyBuf.length}`);
  assert(keyBuf[0] === 0x04, `Expected 0x04 prefix, got ${keyBuf[0]}`);

  // IV should be 12 bytes
  const ivBuf = Buffer.from(payload.iv, "base64");
  assert(ivBuf.length === 12, `Expected 12 bytes, got ${ivBuf.length}`);
});

test("encryptPayload: produces different ciphertext for different data", () => {
  const p1 = encryptPayload({ msg: "hello" }, TEST_KEY);
  const p2 = encryptPayload({ msg: "world" }, TEST_KEY);

  assert(p1.ciphertext !== p2.ciphertext, "Different plaintext should produce different ciphertext");
  assert(p1.iv !== p2.iv, "IV should be random each call");
});

test("encryptPayload: produces different ephemeral keys each call", () => {
  const p1 = encryptPayload({ msg: "test" }, TEST_KEY);
  const p2 = encryptPayload({ msg: "test" }, TEST_KEY);

  assert(p1.ephemeralKey !== p2.ephemeralKey, "Ephemeral key should be different each call");
});

test("encryptPayload: handles empty object", () => {
  const payload = encryptPayload({}, TEST_KEY);
  assert(typeof payload.ciphertext === "string");
  assert(payload.ciphertext.length > 0);
});

test("encryptPayload: handles complex nested data", () => {
  const complex = {
    cwd: "/home/user/project",
    content: "Hello\nWorld",
    nested: { a: 1, b: [2, 3, 4], c: { d: "deep" } },
  };
  const payload = encryptPayload(complex, TEST_KEY);
  assert(typeof payload.ciphertext === "string");
  assert(payload.ciphertext.length > 0);
});

// =========================================================================
// Tests: Envelope Structure Validation
// =========================================================================

console.log("\n--- Envelope Structure ---");

import { randomUUID } from "node:crypto";

function buildTestEnvelope(hookInput) {
  const hookName = hookInput.hook_event_name;
  const eventType = HOOK_EVENT_MAP[hookName] ?? "unknown";
  const data = extractData(hookInput);
  const requestId = randomUUID();

  return {
    type: eventType,
    id: requestId,
    msgType: "event",
    correlationId: hookName === "PreToolUse" ? requestId : null,
    sessionId: hookInput.session_id ?? "",
    from: "desktop",
    timestamp: Date.now(),
    encrypted: false,
    data,
  };
}

test("envelope: SessionStart has correct type and data", () => {
  const env = buildTestEnvelope(MOCK_HOOKS.SessionStart);
  assert(env.type === "session.start");
  assert(env.msgType === "event");
  assert(env.from === "desktop");
  assert(env.correlationId === null);
  assert(env.encrypted === false);
  assert(env.sessionId === "sess-test-001");
  assert(env.data.cwd === "/home/user/myproject");
  // id should be a UUID format
  assert(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(env.id));
});

test("envelope: PreToolUse sets correlationId to request id", () => {
  const env = buildTestEnvelope(MOCK_HOOKS.PreToolUse);
  assert(env.type === "tool.request");
  assert(env.correlationId !== null, "PreToolUse should set correlationId");
  assert(env.correlationId === env.id, "correlationId should equal id for PreToolUse");
});

test("envelope: Stop has message.assistant type", () => {
  const env = buildTestEnvelope(MOCK_HOOKS.Stop);
  assert(env.type === "message.assistant");
  assert(env.correlationId === null);
});

test("envelope: all 8 hook types produce valid envelopes", () => {
  for (const [hookName, mock] of Object.entries(MOCK_HOOKS)) {
    if (hookName.includes("_")) continue; // skip variants like PostToolUse_Edit
    const env = buildTestEnvelope(mock);
    assert(env.type !== "unknown", `Hook ${hookName} should map to a known type`);
    assert(typeof env.id === "string");
    assert(env.msgType === "event");
    assert(env.from === "desktop");
    assert(typeof env.timestamp === "number");
    assert(env.timestamp > 0);
    assert(typeof env.data === "object");
  }
});

// =========================================================================
// Summary
// =========================================================================

console.log(`\n${"=".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log(`${"=".repeat(50)}\n`);

if (failed > 0) {
  process.exit(1);
}
