# CLI-Notify Integration & Delivery Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the plugin-relay protocol mismatch, finalize cross-component integration, and prepare the monorepo for release.

**Architecture:** Three components (Plugin, Relay, Android) communicate via a shared JSON Schema protocol. The plugin enriches Claude Code hook events locally and POSTs complete envelopes to the Relay. The Relay validates, persists, and routes envelopes to Android via WebSocket. The critical gap: the plugin sends complete Envelopes but the relay's `/hook/relay` expects raw HookInput — these wire formats are incompatible and events are silently dropped.

**Tech Stack:** TypeScript (plugin + relay), Kotlin/Compose (Android), JSON Schema (protocol), esbuild (plugin build), Express + ws (relay), SQLite (relay persistence)

---

## Pre-Flight: Critical Integration Gap Analysis

### The Bug

The plugin (Task #4) and relay (Task #2) were implemented independently without verifying the wire format at the `/hook/relay` endpoint:

| Field | Plugin sends | Relay expects |
|-------|-------------|---------------|
| Event discriminator | `type: "session.start"` | `hook_event_name: "SessionStart"` |
| Session identifier | `sessionId: "..."` | `session_id: "..."` |
| Envelope wrapper | Complete Envelope (9 fields) | Raw HookInput (flat fields) |

**Result:** The relay's `handleRelay()` dispatches on `body.hook_event_name`. Since the plugin's envelope has no `hook_event_name` field, it always falls to the default handler and returns `{}` — **all events are silently dropped.**

### Resolution: Two Options

**Option A: Plugin sends raw HookInput, Relay builds Envelopes.**
- Revert plugin to send enriched-but-unwrapped data
- Relay's existing `handleRelay()` stays unchanged
- Downside: The relay needs to know about Claude Code hook event names — tight coupling

**Option B: Relay accepts complete Envelopes, plugin is authoritative.**
- Update relay's `/hook/relay` to accept envelope format
- Plugin is the single place that maps hook names to event types
- Relay is a dumb pipe: validate, route, persist
- **This is the correct choice** — matches the protocol spec principle "relay routes on envelope fields only"

We choose **Option B**.

### The Fix: Dual-format relay endpoint

The relay's `/hook/relay` will detect the format and handle accordingly:

```
if body.hook_event_name exists → legacy raw HookInput → handleRelay() (existing path)
if body.type + body.msgType exist → new Envelope → validateEnvelope() → routeMessage()
if neither → 400 validation error
```

This is backward-compatible (old scripts still work) while supporting the new plugin format.

---

## File Structure After This Plan

```
cli-notify/
├── protocol/
│   └── schema.json                          # (existing) Protocol specification
├── cloud-relay/src/
│   ├── types.ts                             # MODIFY: re-export envelope validators
│   ├── main.ts                              # MODIFY: dual-format /hook/relay
│   ├── hooks.ts                             # MODIFY: add processEnvelope()
│   └── hub.ts                               # MODIFY: ensure buildEnvelope fills all fields
├── cli-notify-plugin/
│   ├── src/
│   │   ├── types.ts                         # MODIFY: sync with relay types.ts
│   │   └── index.ts                         # MODIFY: ensure envelope is valid per schema
│   ├── scripts/
│   │   └── relay-forward.mjs                # REBUILD: from updated source
│   └── hooks/hooks.json                     # (unchanged, already correct)
└── docs/
    └── INTEGRATION.md                       # CREATE: integration verification document
```

---

### Task 1: Fix Plugin-Relay Envelope Mismatch in Relay

**Files:**
- Modify: `cloud-relay/src/types.ts:22-37`
- Modify: `cloud-relay/src/main.ts:189-211`
- Modify: `cloud-relay/src/hooks.ts:1-3` (add import)

**Context:** The relay's `/hook/relay` endpoint currently delegates to `handleRelay()` which dispatches on `hook_event_name`. It needs to support the plugin's complete-envelope format.

- [ ] **Step 1: Add envelope validation helper to types.ts**

Add after line 37 of `cloud-relay/src/types.ts`:

```typescript
/** Validates that an incoming object looks like a valid Envelope. */
export function isValidEnvelope(obj: unknown): obj is Envelope {
  if (!obj || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return (
    typeof o.type === "string" &&
    typeof o.msgType === "string" &&
    ["request", "response", "event"].includes(o.msgType as string) &&
    typeof o.sessionId === "string" &&
    typeof o.encrypted === "boolean" &&
    typeof o.data === "object" && o.data !== null
  );
}
```

- [ ] **Step 2: Modify /hook/relay in main.ts to support dual-format**

Replace lines 189-211 of `cloud-relay/src/main.ts`:

```typescript
// ── Unified /hook/relay endpoint ──
// Accepts two formats:
//   1. New envelope format (from plugin v2): { type, msgType, sessionId, encrypted, data, ... }
//   2. Legacy raw hook format (backward compat): { hook_event_name, session_id, ... }
app.post("/hook/relay", (req, res) => {
  try {
    const userId = requireToken(req);
    const body = req.body;

    // New envelope format — plugin sends complete envelopes
    if (isValidEnvelope(body)) {
      const envelope = body as Envelope;

      // Fill in fields the plugin might have omitted
      const complete = hub.buildEnvelope({
        type: envelope.type,
        msgType: envelope.msgType,
        correlationId: envelope.correlationId ?? null,
        sessionId: envelope.sessionId,
        from: envelope.from ?? Actor.DESKTOP,
        encrypted: envelope.encrypted ?? false,
        data: envelope.data,
      });

      // Update session metadata for known event types
      if (envelope.type === "session.start") {
        const cwd = (envelope.data as Record<string, unknown>).cwd;
        if (cwd && typeof cwd === "string") {
          hub.setSessionMeta(userId, envelope.sessionId, { cwd, startedAt: Date.now(), status: "active" });
        }
      } else if (envelope.type === "session.end") {
        hub.endSession(userId, envelope.sessionId);
      } else if (envelope.type === "message.assistant") {
        hub.setSessionMeta(userId, envelope.sessionId, { status: "idle" });
      }

      hub.routeMessage(userId, complete);
      res.json({ status: "ok" });
      return;
    }

    // Legacy raw hook format — backward compatible
    if (body.hook_event_name && body.session_id) {
      const result = handleRelay(body as HookInput, userId, hub);
      res.json(result);
      return;
    }

    validationError(res, "Missing envelope fields (type+msgType) or hook_event_name");
  } catch (err: any) {
    if (err.statusCode === 403) {
      authError(res, "AUTH_INVALID_TOKEN", err.message);
    } else {
      console.error("[hook/relay] Error:", err);
      internalError(res, "Hook processing failed", err.message);
    }
  }
});
```

- [ ] **Step 3: Add imports to main.ts**

Confirm `main.ts` has these imports at the top. If any are missing, add them:

```typescript
import { MsgType, Actor, isValidEnvelope } from "./types.js";
// These should already exist:
import { handleRelay } from "./hooks.js";
import { validationError, authError, internalError } from "./errors.js";
```

Check existing imports at line 54-57 and add any missing ones.

- [ ] **Step 4: Verify relay builds clean**

Run: `cd cloud-relay && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add cloud-relay/src/types.ts cloud-relay/src/main.ts
git commit -m "fix(relay): support envelope format in /hook/relay endpoint

Previously /hook/relay only handled raw HookInput (hook_event_name dispatch).
Now detects the wire format — envelopes with type+msgType go through
hub.routeMessage(), legacy hook_event_name bodies go through handleRelay().

This fixes the plugin-relay integration where the v2 plugin sends complete
Envelopes but the relay was silently dropping them."
```

---

### Task 2: Sync Plugin TypeScript Types with Relay

**Files:**
- Modify: `cli-notify-plugin/src/types.ts`

**Context:** The plugin's `types.ts` and the relay's `types.ts` were developed independently. They should share the same `Envelope`, `MsgType`, `EncryptedPayload` definitions. The relay's types.ts is more complete (has validators, constants, IncomingEnvelope type).

- [ ] **Step 1: Update plugin types.ts to match relay's canonical type definitions**

The key changes to `cli-notify-plugin/src/types.ts`:

1. Change `Sender` type to match relay's `Actor`:
```typescript
export type Sender = "desktop" | "mobile" | "server";
```

2. Update `EncryptedPayload` to match (already matches — verify):
```typescript
export interface EncryptedPayload {
  ephemeralKey: string;
  iv: string;
  ciphertext: string;
}
```

3. Add `IncomingEnvelope` type (partial envelope for sending):
```typescript
export type IncomingEnvelope = Partial<Pick<Envelope, "id" | "timestamp" | "from">> &
  Omit<Envelope, "id" | "timestamp" | "from">;
```

No structural changes are needed since the plugin's types already match the schema. This task verifies alignment and adds the `IncomingEnvelope` type for future use.

- [ ] **Step 2: Verify plugin types pass typecheck**

Run: `cd cli-notify-plugin && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Rebuild relay-forward.mjs**

Run: `cd cli-notify-plugin && node build.mjs`
Expected: "Build completed: scripts/relay-forward.mjs"

- [ ] **Step 4: Commit**

```bash
git add cli-notify-plugin/src/types.ts cli-notify-plugin/scripts/relay-forward.mjs
git commit -m "chore(plugin): sync TypeScript types with relay's canonical definitions"
```

---

### Task 3: Cross-Component Protocol Verification

**Files:**
- Create: `docs/INTEGRATION.md`

**Context:** Before declaring the refactor complete, verify that the core data flow works end-to-end by tracing each event type through all three components. This verification is static — no live connections needed.

- [ ] **Step 1: Create integration verification document**

Create `docs/INTEGRATION.md`:

````markdown
# CLI-Notify Integration Verification

## Wire Format Contract

All components agree on the protocol defined in `protocol/schema.json`.

### Envelope structure
| Field | Plugin (relay-forward.mjs) | Relay (/hook/relay) | Android (EventParser.kt) |
|-------|---------------------------|---------------------|--------------------------|
| type | Sets from HOOK_EVENT_MAP | Passes through | deserializes via SessionEventSerializer |
| id | Generates UUID | Fills if missing | Reads (not critical) |
| msgType | Sets to "event" | Validates enum | Ignores (ignoreUnknownKeys=true) |
| correlationId | Sets for PreToolUse | Passes through | Reads as requestId |
| sessionId | From hook input | Required field | Reads |
| from | Sets to "desktop" | Passes through | Ignores |
| timestamp | Sets Date.now() | Fills if missing | Reads |
| encrypted | boolean | Passes through | Reads, triggers CryptoManager |
| data | Type-specific payload | Passes through (opaque) | Deserializes as polymorphic |

### Event Type Mapping
| Hook Event | Plugin Event Type | Relay Action | Android Sealed Class |
|-----------|-------------------|-------------|---------------------|
| SessionStart | session.start | setSessionMeta | SessionStartEvent |
| UserPromptSubmit | message.user | broadcast | MessageUserEvent |
| PreToolUse | tool.request | broadcast + corrId | ToolRequestEvent |
| PostToolUse | tool.result | broadcast | ToolResultEvent |
| PermissionRequest | tool.permission_request | broadcast | ToolPermissionRequestEvent |
| Stop | message.assistant + notification | setMeta idle | MessageAssistantEvent + NotificationEvent |
| SessionEnd | session.end | endSession | SessionEndEvent |
| Notification | notification | broadcast | NotificationEvent |

### E2EE Crypto Verification
| Step | Plugin (crypto.ts) | Android (CryptoManager.kt) |
|------|-------------------|---------------------------|
| Curve | prime256v1 (P-256) | secp256r1 (P-256) |
| ECDH | createECDH() → ephemeral | KeyAgreement ECDH |
| HKDF info | "cli-notify-v1" | "cli-notify-v1" |
| HKDF salt | 32 zero bytes | 32 zero bytes |
| AEAD | aes-256-gcm, 12-byte IV | AES/GCM/NoPadding, 12-byte IV |
| Auth tag | 16 bytes appended | 16 bytes, split before decrypt |
| Key encoding | Raw uncompressed (65 bytes) | 0x04 || X || Y (65 bytes) |

### Verification Checklist
- [ ] Plugin builds and typechecks: `cd cli-notify-plugin && node build.mjs && npx tsc --noEmit`
- [ ] Relay builds and typechecks: `cd cloud-relay && npx tsc --noEmit`
- [ ] Android builds: `cd android && ./gradlew assembleDebug`
- [ ] Envelope has all 9 required fields per schema
- [ ] HOOK_EVENT_MAP entries match schema's EventType enum
- [ ] Encryption params match between crypto.ts and CryptoManager.kt
- [ ] Android EventParser handles all 8 event types + auth_success + sync
- [ ] Relay /hook/relay handles both envelope and legacy formats
- [ ] No unused hook endpoints remain on relay (8 individual /hook/* paths)
````

- [ ] **Step 2: Run static verification commands**

Run each verification command and fix any issues found:

```bash
cd cloud-relay && npx tsc --noEmit
cd cli-notify-plugin && npx tsc --noEmit && node build.mjs
```

- [ ] **Step 3: Commit**

```bash
git add docs/INTEGRATION.md
git commit -m "docs: add integration verification document with protocol contract"
```

---

### Task 4: Final Cleanup — Remove Legacy Hook Endpoints from Relay

**Files:**
- Modify: `cloud-relay/src/main.ts`

**Context:** The relay has 8 individual `/hook/session-start`, `/hook/user-prompt`, etc. endpoints from the pre-refactor design. These are now dead code since the plugin only calls `/hook/relay`. Removing them is cleanup, not functional change.

- [ ] **Step 1: Remove individual hook endpoint registrations**

In `cloud-relay/src/main.ts`, remove lines that register individual hook endpoints. These are the lines like:
```typescript
app.post("/hook/session-start", hookWrapper(handleSessionStart));
app.post("/hook/user-prompt", hookWrapper(handleUserPrompt));
// ... etc
```

Find the exact lines with grep:
Run: `cd cloud-relay && grep -n "app.post.*hook/" src/main.ts`

Expected the old individual hook registrations and the unified `/hook/relay`. Remove only the individual ones, keeping `/hook/relay`.

- [ ] **Step 2: Remove the `hookWrapper` function**

If no other routes use `hookWrapper`, remove it from main.ts entirely (it was only used by the individual hook endpoints).

Run: `cd cloud-relay && grep -n "hookWrapper" src/main.ts`

If `hookWrapper` is only defined and only called by the individual endpoints being removed, delete the function definition.

- [ ] **Step 3: Clean up unused imports in main.ts**

After removing the hook endpoints, check if `handleSessionStart`, `handleUserPrompt`, etc. from hooks.js are still imported but unused.

Run: `cd cloud-relay && npx tsc --noEmit`

If there are "imported but not used" errors, remove the unused imports from the import statement at the top of main.ts. Only keep the hooks imports that are still used (`handleRelay`).

- [ ] **Step 4: Verify relay builds clean**

Run: `cd cloud-relay && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 5: Regenerate dist**

Run: `cd cloud-relay && npx tsc`
Expected: No errors, dist/ directory updated.

- [ ] **Step 6: Commit**

```bash
git add cloud-relay/src/main.ts cloud-relay/dist/
git commit -m "refactor(relay): remove legacy individual hook endpoints

All events now flow through /hook/relay which accepts both
new envelope format and legacy HookInput format. The 8
individual endpoints are dead code — the plugin never calls them."
```

---

### Task 5: Final Monorepo Verification

**Files:** (read-only verification, no file changes)

**Context:** Run the full build pipeline across all three components to confirm nothing is broken.

- [ ] **Step 1: Verify relay build**

```bash
cd cloud-relay && npx tsc --noEmit && npx tsc
```

Expected: Typecheck and compile clean.

- [ ] **Step 2: Verify plugin build**

```bash
cd cli-notify-plugin && npx tsc --noEmit && node build.mjs
```

Expected: Typecheck clean. Build output: "Build completed: scripts/relay-forward.mjs"

- [ ] **Step 3: Verify Android build**

```bash
cd android && ./gradlew assembleDebug
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Verify protocol schema is valid JSON**

```bash
cd protocol && node -e "const s = require('fs').readFileSync('schema.json','utf8'); JSON.parse(s); console.log('Valid JSON')"
```

Expected: "Valid JSON"

- [ ] **Step 5: Final git status check**

```bash
cd cli-notify && git status
```

Expected: No unexpected modified or untracked files.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: final integration verification — all components build clean

Verified:
- Relay typechecks and compiles (dual-format /hook/relay)
- Plugin typechecks and rebuilds relay-forward.mjs
- Android assembles successfully
- Protocol schema is valid JSON
- All hook events map to correct protocol event types"
```

---

## Self-Review

### 1. Spec Coverage

| Requirement from spec | Covered by |
|----------------------|-----------|
| Plugin sends envelopes per schema | Task #4 (already done) — verified in Task 2-3 |
| Relay routes on msgType/correlationId | Task #2 (already done) — fixed in Task 1 |
| E2EE matching between plugin and Android | Verified in Task 3 (static comparison) |
| Single unified endpoint /hook/relay | Task 1 (adds envelope support) + Task 4 (removes legacy) |
| All 8 hook events mapped | Verified in Task 3 integration matrix |
| Offline queue SQLite persistence | Task #2 (already done) |
| All components build clean | Task 5 final verification |

### 2. Placeholder Scan

No TBDs, TODOs, or "implement later" in this plan. All code blocks are complete.

### 3. Type Consistency

- `Envelope` types verified against `protocol/schema.json` across all tasks
- `isValidEnvelope()` signature consistent with `Envelope` interface
- `hub.buildEnvelope()` parameters match the `IncomingEnvelope` type
- Event type strings consistent across plugin HOOK_EVENT_MAP, relay EventType enum, and Android SessionEventSerializer

No issues found.
