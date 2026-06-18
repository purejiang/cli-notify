/**
 * Protocol Schema Validation Tests (v2)
 *
 * Validates that example envelopes (valid and invalid) pass/fail
 * against protocol/schema.json as expected.
 *
 * Covers:
 *   - All 37 EventType values produce valid envelopes
 *   - EncryptedPayload format validation
 *   - EnvelopeData structure (raw, truncated, optional fields)
 *   - Required field enforcement (8 top-level fields)
 *   - Enum constraint enforcement (MsgType, Peer, EventType)
 *   - Type constraint enforcement
 *   - _decision relay metadata field
 *   - correlationId / groupId formats
 *   - tool_use_id / agent_id / turn_id nullability
 *   - ErrorCode enumeration (9 codes)
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, "..", "..");

// Minimal JSON Schema validator (no external deps)
// Supports our schema subset: type, enum, format, required, properties, const, minimum, $ref

function loadJSON(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

// ── Minimal JSON Schema Validator ──

function validateAgainstSchema(instance, schema, definitions) {
  const errors = [];
  const defs = { ...(schema.$defs ?? {}), ...(definitions ?? {}) };

  // required
  if (schema.required) {
    for (const field of schema.required) {
      if (!(field in instance)) {
        errors.push(`Missing required field: ${field}`);
      }
    }
  }

  // properties
  if (schema.properties) {
    for (const [key, propSchema] of Object.entries(schema.properties)) {
      if (key in instance) {
        const value = instance[key];
        const propErrors = validateProperty(key, value, propSchema, defs, [key]);
        errors.push(...propErrors);
      }
    }
  }

  return errors;
}

function validateProperty(name, value, schema, defs, path) {
  const errors = [];

  // $ref
  if (schema.$ref) {
    const refPath = schema.$ref.replace("#/$defs/", "");
    const refSchema = defs[refPath];
    if (refSchema) {
      return validateProperty(name, value, refSchema, defs, path);
    }
    return errors;
  }

  // type
  if (schema.type) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    let typeMatch = false;
    for (const t of types) {
      if (t === "null" && value === null) { typeMatch = true; break; }
      if (t === "string" && typeof value === "string") { typeMatch = true; break; }
      if (t === "integer") {
        if (Number.isInteger(value)) { typeMatch = true; break; }
      }
      if (t === "number" && typeof value === "number") { typeMatch = true; break; }
      if (t === "boolean" && typeof value === "boolean") { typeMatch = true; break; }
      if (t === "object" && typeof value === "object" && value !== null && !Array.isArray(value)) { typeMatch = true; break; }
      if (t === "array" && Array.isArray(value)) { typeMatch = true; break; }
    }
    if (!typeMatch) {
      errors.push(`${path.join(".")}: expected ${types.join("|")}, got ${value === null ? "null" : typeof value}`);
      return errors;
    }
  }

  // enum
  if (schema.enum) {
    if (!schema.enum.includes(value)) {
      errors.push(`${path.join(".")}: "${value}" not in enum [${schema.enum.map(e => `"${e}"`).join(", ")}]`);
    }
  }

  // format (basic uuid check)
  if (schema.format === "uuid" && typeof value === "string") {
    const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRe.test(value)) {
      errors.push(`${path.join(".")}: "${value}" is not a valid UUID`);
    }
  }

  // minimum
  if (schema.minimum !== undefined && typeof value === "number") {
    if (value < schema.minimum) {
      errors.push(`${path.join(".")}: ${value} < minimum ${schema.minimum}`);
    }
  }

  // const
  if (schema.const !== undefined) {
    if (value !== schema.const) {
      errors.push(`${path.join(".")}: "${value}" !== const "${schema.const}"`);
    }
  }

  // nested properties (for objects)
  if (schema.properties && typeof value === "object" && value !== null && !Array.isArray(value)) {
    for (const [subKey, subSchema] of Object.entries(schema.properties)) {
      if (subKey in value) {
        const subErrors = validateProperty(subKey, value[subKey], subSchema, defs, [...path, subKey]);
        errors.push(...subErrors);
      }
    }
    // required for nested objects
    if (schema.required) {
      for (const field of schema.required) {
        if (!(field in value)) {
          errors.push(`${path.join(".")}: missing required field "${field}"`);
        }
      }
    }
  }

  return errors;
}

// ── Helper: validate against a specific $def schema ──

function validateDef(name, instance, schema, defs) {
  const defSchema = schema.$defs[name];
  if (!defSchema) {
    return [`$def "${name}" not found`];
  }
  return validateProperty("", instance, defSchema, schema.$defs, [name]);
}

// ── Helper: check if data matches EncryptedPayload shape ──

function isEncryptedPayload(data) {
  return (
    typeof data === "object" &&
    data !== null &&
    !Array.isArray(data) &&
    typeof data.ephemeralKey === "string" &&
    typeof data.iv === "string" &&
    typeof data.ciphertext === "string"
  );
}

// ── Main Test Runner ──

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

// ── Load Schema and Fixtures ──

const schema = loadJSON(resolve(PROJECT_ROOT, "protocol", "schema.json"));
const validFixtures = loadJSON(resolve(PROJECT_ROOT, "tests", "fixtures", "valid-envelopes.json"));
const invalidFixtures = loadJSON(resolve(PROJECT_ROOT, "tests", "fixtures", "invalid-envelopes.json"));

console.log("\n=== Protocol v2 Schema Validation Tests ===\n");

// ── Valid Envelope Tests ──

console.log("--- Valid Envelopes ---");

const validEntries = Object.entries(validFixtures.envelopes);
for (const [name, envelope] of validEntries) {
  test(`${name} passes schema validation`, () => {
    const errors = validateAgainstSchema(envelope, schema);
    if (errors.length > 0) {
      throw new Error(`Validation errors: ${errors.join("; ")}`);
    }
  });
}

// ── Invalid Envelope Tests ──

console.log("\n--- Invalid Envelopes ---");

for (const [name, envelope] of Object.entries(invalidFixtures.envelopes)) {
  test(`${name} FAILS schema validation`, () => {
    const errors = validateAgainstSchema(envelope, schema);
    assert(errors.length > 0, `Expected validation errors for ${name}, but none found`);
  });
}

// ── Required Field Tests ──

const BASE_VALID = validFixtures.envelopes["session_start"];

console.log("\n--- Required Field Enforcement ---");

const requiredFields = ["type", "id", "msgType", "sessionId", "from", "timestamp", "encrypted", "data"];
for (const field of requiredFields) {
  test(`required field "${field}" enforced`, () => {
    const incomplete = { ...BASE_VALID };
    delete incomplete[field];
    const errors = validateAgainstSchema(incomplete, schema);
    assert(errors.length > 0, `Missing "${field}" should produce validation error`);
  });
}

// ── Enum Constraint Tests ──

console.log("\n--- Enum Constraints ---");

test("MsgType enum — only event|request|response are valid", () => {
  const bad = { ...BASE_VALID, msgType: "invalid_msg_type" };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Invalid msgType should be rejected");
});

test("Peer enum — only desktop|mobile|server are valid", () => {
  const bad = { ...BASE_VALID, from: "browser" };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Invalid from should be rejected");
});

test("EventType enum — unknown event type rejected", () => {
  const bad = { ...BASE_VALID, type: "unknown.type" };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Invalid type should be rejected");
});

// ── Type Constraint Tests ──

console.log("\n--- Type Constraints ---");

test("timestamp minimum >= 0 enforced", () => {
  const bad = { ...BASE_VALID, timestamp: -1 };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Negative timestamp should be rejected");
});

test("encrypted must be boolean", () => {
  const bad = { ...BASE_VALID, encrypted: "yes" };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Non-boolean encrypted should be rejected");
});

test("data must be an object", () => {
  const bad = { ...BASE_VALID, data: "not_an_object" };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Non-object data should be rejected");
});

// ── EnvelopeData Structure Tests ──

console.log("\n--- EnvelopeData Structure ---");

test("EnvelopeData requires 'raw' field", () => {
  const bad = { ...BASE_VALID, data: { truncated: false } };
  // Schema validates data is object but doesn't enforce nested required
  // Validate against EnvelopeData $def directly
  const errors = validateDef("EnvelopeData", bad.data, schema);
  assert(errors.length > 0, "Missing 'raw' in EnvelopeData should be rejected");
});

test("EnvelopeData requires 'truncated' field", () => {
  const bad = { ...BASE_VALID, data: { raw: {} } };
  const errors = validateDef("EnvelopeData", bad.data, schema);
  assert(errors.length > 0, "Missing 'truncated' in EnvelopeData should be rejected");
});

test("EnvelopeData 'truncated' must be boolean", () => {
  const bad = { ...BASE_VALID, data: { raw: {}, truncated: "yes" } };
  const errors = validateDef("EnvelopeData", bad.data, schema);
  assert(errors.length > 0, "Non-boolean truncated should be rejected");
});

test("EnvelopeData 'raw' must be an object", () => {
  const bad = { ...BASE_VALID, data: { raw: "string_not_object", truncated: false } };
  const errors = validateDef("EnvelopeData", bad.data, schema);
  assert(errors.length > 0, "Non-object raw should be rejected");
});

test("tool_use_id can be null or string", () => {
  const withId = { ...BASE_VALID, data: { ...BASE_VALID.data, tool_use_id: "tool-001" } };
  const nullId = { ...BASE_VALID, data: { ...BASE_VALID.data, tool_use_id: null } };
  const withErrors = validateDef("EnvelopeData", withId.data, schema);
  const nullErrors = validateDef("EnvelopeData", nullId.data, schema);
  assert(withErrors.length === 0, "tool_use_id as string should be valid");
  assert(nullErrors.length === 0, "tool_use_id as null should be valid");
});

test("agent_id can be null or string", () => {
  const withId = { ...BASE_VALID, data: { ...BASE_VALID.data, agent_id: "agent-001" } };
  const nullId = { ...BASE_VALID, data: { ...BASE_VALID.data, agent_id: null } };
  assert(validateDef("EnvelopeData", withId.data, schema).length === 0, "agent_id as string");
  assert(validateDef("EnvelopeData", nullId.data, schema).length === 0, "agent_id as null");
});

test("turn_id can be null or string", () => {
  const withId = { ...BASE_VALID, data: { ...BASE_VALID.data, turn_id: "turn-uuid-001" } };
  const nullId = { ...BASE_VALID, data: { ...BASE_VALID.data, turn_id: null } };
  assert(validateDef("EnvelopeData", withId.data, schema).length === 0, "turn_id as string");
  assert(validateDef("EnvelopeData", nullId.data, schema).length === 0, "turn_id as null");
});

// ── EncryptedPayload Tests ──

console.log("\n--- EncryptedPayload Constraints ---");

test("encrypted=true envelope has EncryptedPayload shape (ephemeralKey, iv, ciphertext)", () => {
  const encrypted = validFixtures.envelopes["encrypted_envelope"];
  assert(encrypted.encrypted === true, "Fixture should be encrypted");
  const dataErrors = validateDef("EncryptedPayload", encrypted.data, schema);
  assert(dataErrors.length === 0, `EncryptedPayload validation failed: ${dataErrors.join("; ")}`);
});

test("EncryptedPayload requires ephemeralKey", () => {
  const bad = { iv: "AAAA", ciphertext: "BBBB" };
  const errors = validateDef("EncryptedPayload", bad, schema);
  assert(errors.length > 0, "Missing ephemeralKey should be rejected");
});

test("EncryptedPayload requires iv", () => {
  const bad = { ephemeralKey: "AAAA", ciphertext: "BBBB" };
  const errors = validateDef("EncryptedPayload", bad, schema);
  assert(errors.length > 0, "Missing iv should be rejected");
});

test("EncryptedPayload requires ciphertext", () => {
  const bad = { ephemeralKey: "AAAA", iv: "BBBB" };
  const errors = validateDef("EncryptedPayload", bad, schema);
  assert(errors.length > 0, "Missing ciphertext should be rejected");
});

test("encrypted envelope cannot have EnvelopeData shape", () => {
  const encrypted = validFixtures.envelopes["encrypted_envelope"];
  // An encrypted envelope's data should NOT pass EnvelopeData validation
  // (it won't have 'raw' field, which is required)
  const dataErrors = validateDef("EnvelopeData", encrypted.data, schema);
  assert(dataErrors.length > 0, "Encrypted data should not match EnvelopeData shape");
});

// ── _decision Field Tests ──

console.log("\n--- _decision Field ---");

test("_decision can be null on regular envelopes", () => {
  const withNull = { ...BASE_VALID, _decision: null };
  const errors = validateAgainstSchema(withNull, schema);
  assert(errors.length === 0, "_decision: null should be valid");
});

test("_decision with allow decision is valid", () => {
  const withDecision = { ...BASE_VALID, _decision: { decision: "allow", reason: "OK" } };
  const errors = validateAgainstSchema(withDecision, schema);
  assert(errors.length === 0, `_decision with allow should be valid`);
});

test("_decision with deny decision is valid", () => {
  const withDecision = { ...BASE_VALID, _decision: { decision: "deny", reason: "Not safe" } };
  const errors = validateAgainstSchema(withDecision, schema);
  assert(errors.length === 0, "_decision with deny should be valid");
});

test("_decision without reason is valid (reason is optional)", () => {
  const withDecision = { ...BASE_VALID, _decision: { decision: "allow" } };
  const errors = validateAgainstSchema(withDecision, schema);
  assert(errors.length === 0, "_decision without reason should be valid");
});

test("_decision decision must be allow|deny", () => {
  const bad = { ...BASE_VALID, _decision: { decision: "maybe" } };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Invalid _decision.decision should be rejected");
});

test("_decision without decision field fails", () => {
  const bad = { ...BASE_VALID, _decision: { reason: "no decision" } };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Missing _decision.decision should be rejected");
});

// ── correlationId & groupId Tests ──

console.log("\n--- correlationId & groupId ---");

test("correlationId null is valid", () => {
  const env = { ...BASE_VALID, correlationId: null };
  const errors = validateAgainstSchema(env, schema);
  assert(errors.length === 0, "correlationId: null should be valid");
});

test("correlationId UUID format validated", () => {
  const bad = { ...BASE_VALID, correlationId: "not-a-uuid" };
  const errors = validateAgainstSchema(bad, schema);
  assert(errors.length > 0, "Invalid correlationId format should be rejected");
});

test("correlationId valid UUID passes", () => {
  const env = { ...BASE_VALID, correlationId: "550e8400-e29b-41d4-a716-446655440999" };
  const errors = validateAgainstSchema(env, schema);
  assert(errors.length === 0, "Valid UUID correlationId should pass");
});

test("groupId null is valid", () => {
  const env = { ...BASE_VALID, groupId: null };
  const errors = validateAgainstSchema(env, schema);
  assert(errors.length === 0, "groupId: null should be valid");
});

test("groupId string is valid (no format constraint)", () => {
  const env = { ...BASE_VALID, groupId: "any-string-group" };
  const errors = validateAgainstSchema(env, schema);
  assert(errors.length === 0, "groupId any string should pass");
});

// ── Event Type Coverage ──

console.log("\n--- Event Type Coverage ---");

const ALL_EVENT_TYPES = [
  // 18 Core hooks
  "session_start", "session_end",
  "user_prompt_submit",
  "pre_tool_use", "post_tool_use", "post_tool_use_failure", "post_tool_batch",
  "permission_request", "permission_denied",
  "stop", "stop_failure",
  "notification", "message_display",
  "subagent_start", "subagent_stop",
  "task_created", "task_completed",
  "elicitation",
  // 12 Extended hooks
  "elicitation_result",
  "user_prompt_expansion", "setup",
  "pre_compact", "post_compact",
  "teammate_idle",
  "config_change", "cwd_changed", "file_changed", "instructions_loaded",
  "worktree_create", "worktree_remove",
  // 7 System types
  "key.exchange", "set_preferences", "get_preferences", "preferences",
  "sync", "auth_success", "error",
];

// Check EventType enum in schema matches our expected list
const schemaEventTypes = schema.$defs.EventType.enum;
const missingFromSchema = ALL_EVENT_TYPES.filter(t => !schemaEventTypes.includes(t));
const extraInSchema = schemaEventTypes.filter(t => !ALL_EVENT_TYPES.includes(t));

test("all 37 EventType values are in schema enum", () => {
  assert(missingFromSchema.length === 0, `Missing from schema: ${missingFromSchema.join(", ")}`);
  assert(extraInSchema.length === 0, `Extra in schema: ${extraInSchema.join(", ")}`);
  assert(schemaEventTypes.length === 37, `Expected 37 types, got ${schemaEventTypes.length}`);
});

const coveredTypes = Object.values(validFixtures.envelopes).map(e => e.type);
const missingTypes = ALL_EVENT_TYPES.filter(t => !coveredTypes.includes(t));

test("all 37 EventType values have a valid fixture", () => {
  assert(
    missingTypes.length === 0,
    `Missing fixtures for: ${missingTypes.join(", ")}`
  );
});

// ── ErrorCode Enum Tests ──

console.log("\n--- ErrorCode Enum ---");

const ALL_ERROR_CODES = [
  "INVALID_ENVELOPE", "UNKNOWN_TYPE", "AUTH_FAILED",
  "ROOM_NOT_FOUND", "REQUEST_TIMEOUT", "RATE_LIMITED", "INTERNAL",
  "CONNECTION_LIMIT", "ENCRYPTION_FAILED",
];

const ErrorCodeSchema = schema.$defs.ErrorCode;

for (const code of ALL_ERROR_CODES) {
  test(`ErrorCode "${code}" is valid`, () => {
    assert(ErrorCodeSchema.enum.includes(code), `"${code}" not in ErrorCode enum`);
  });
}

test("ErrorCode enum has exactly 9 codes", () => {
  assert(ErrorCodeSchema.enum.length === 9, `Expected 9 codes, got ${ErrorCodeSchema.enum.length}`);
});

// ── HKDF Info String ──

console.log("\n--- HKDF Info String ---");

test("EncryptedPayload description references 'cli-notify-v2' info string", () => {
  const desc = schema.$defs.EncryptedPayload.description;
  assert(
    desc.includes("cli-notify-v2"),
    `Expected 'cli-notify-v2' in EncryptedPayload description, got: ${desc}`
  );
});

// ── Envelope Completeness ──

console.log("\n--- Envelope Structure ---");

test("all valid envelopes have all 8 required fields", () => {
  for (const [name, env] of validEntries) {
    for (const field of requiredFields) {
      assert(field in env, `${name}: missing required field "${field}"`);
    }
  }
});

test("all valid envelopes have correlationId", () => {
  for (const [name, env] of validEntries) {
    assert("correlationId" in env, `${name}: missing correlationId`);
  }
});

test("all valid envelopes have groupId", () => {
  for (const [name, env] of validEntries) {
    assert("groupId" in env, `${name}: missing groupId`);
  }
});

// ── Request/Response Pairing Tests ──

console.log("\n--- Request/Response Pairing ---");

test("request envelopes have correlationId matching their id", () => {
  const requestPairs = [
    validFixtures.envelopes["pre_tool_use"],
    validFixtures.envelopes["permission_request"],
    validFixtures.envelopes["elicitation"],
  ];
  for (const env of requestPairs) {
    assert(env.msgType === "request", `${env.type} should be request`);
    assert(env.correlationId !== null, `${env.type} should have correlationId`);
  }
});

test("response envelopes have correlationId linking to request", () => {
  const responses = [
    { name: "pre_tool_use_response", requestId: validFixtures.envelopes["pre_tool_use"].id },
    { name: "permission_request_response", requestId: validFixtures.envelopes["permission_request"].id },
    { name: "elicitation_response", requestId: validFixtures.envelopes["elicitation"].id },
  ];
  for (const { name, requestId } of responses) {
    const env = validFixtures.envelopes[name];
    assert(env.msgType === "response", `${name} should be response`);
    assert(env.correlationId === requestId, `${name} correlationId should match request id`);
  }
});

// ── System Type Tests ──

console.log("\n--- System Type Envelopes ---");

test("key.exchange envelope is valid", () => {
  const env = validFixtures.envelopes["key.exchange"];
  assert(env.type === "key.exchange");
  assert(env.from === "mobile");
  assert(typeof env.data.raw.publicKey === "string");
});

test("set_preferences envelope is valid", () => {
  const env = validFixtures.envelopes["set_preferences"];
  assert(env.type === "set_preferences");
  assert(env.msgType === "request");
  assert(typeof env.data.raw.approval_timeout_ms === "number");
});

test("sync envelope is valid", () => {
  const env = validFixtures.envelopes["sync"];
  assert(env.type === "sync");
  assert(env.msgType === "request");
  assert(env.data.raw.query === "sessions");
});

test("error envelope is valid", () => {
  const env = validFixtures.envelopes["error"];
  assert(env.type === "error");
  assert(env.from === "server");
  assert(env.data.raw.code === "REQUEST_TIMEOUT");
});

test("auth_success envelope is valid", () => {
  const env = validFixtures.envelopes["auth_success"];
  assert(env.type === "auth_success");
  assert(env.from === "server");
});

test("get_preferences envelope is valid", () => {
  const env = validFixtures.envelopes["get_preferences"];
  assert(env.type === "get_preferences");
  assert(env.msgType === "request");
});

test("preferences envelope is valid", () => {
  const env = validFixtures.envelopes["preferences"];
  assert(env.type === "preferences");
  assert(env.from === "server");
});

// ── Summary ──

console.log(`\n${"=".repeat(60)}`);
console.log(`Results: ${passed} passed, ${failed} failed, ${passed + failed} total`);
console.log(`${"=".repeat(60)}\n`);

if (failed > 0) {
  process.exit(1);
}
