# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CLI-Notify mirrors Claude Code desktop sessions to Android in real time — view messages, approve/deny tool permissions, and send replies from your phone.

```
Claude Code Desktop ──Plugin (HTTP)──▶ Cloud Relay (VPS) ──WebSocket──▶ Android App
```

The repo is a **monorepo with 3 git submodules**: `android/`, `cli-notify-plugin/`, `cloud-relay/`. Protocol is defined in `protocol/schema.json` — the single source of truth for the Envelope message format shared across all three components.

## Build & Test Commands

### Cloud Relay (Python/FastAPI)

```bash
cd cloud-relay
pip install -r requirements.txt

# Run relay
python main.py

# Run tests
cd ../tests && python3 -m pytest plugin/ -v
```

Environment variables: `CLOUD_RELAY_PORT` (8765), `CLOUD_RELAY_HOST` (0.0.0.0), `RELAY_PUBLIC_HOST`, `PAIRING_KEY`, `JWT_SECRET`, `SQLITE_PATH`.

### Plugin (Python, minimal deps)

```bash
cd cli-notify-plugin
pip install -r requirements.txt

# Run plugin (invoked by Claude Code hooks, not standalone)
python scripts/relay-forward.py
```

The plugin is a single-file Python script (`scripts/relay-forward.py`) with dependencies on `httpx` and `cryptography`. No build step required.

### Running All Tests

```bash
# Protocol schema validation (58 tests, zero deps)
cd tests && node protocol/schema-validation.test.js

# Plugin integration tests (39 tests)
cd tests && python3 -m pytest plugin/ -v

# All tests
cd tests && npm test
```

### Docker

```bash
cd cloud-relay
docker-compose up -d
```

## Architecture

### Protocol (`protocol/schema.json`)

Every message is an **Envelope** with these required fields:
- `type` — event type discriminator (15 types: `session.start`, `message.user`, `tool.request`, etc.)
- `id` — UUID v4
- `msgType` — `"request"` | `"response"` | `"event"`
- `sessionId`, `from` (`"desktop"` | `"mobile"` | `"server"`), `timestamp`, `encrypted`, `data`

E2EE uses ECDH P-256 + AES-256-GCM. When `encrypted: true`, `data` contains `{ephemeralKey, iv, ciphertext}` (all base64). The relay never decrypts — it's a blind forwarder.

### Cloud Relay (`cloud-relay/` — Python/FastAPI)

- `app/main.py` — FastAPI app creation, WebSocket `/ws` endpoint, startup/shutdown lifecycle, entry point
- `app/routers.py` — All HTTP route handlers: `/auth/*`, `/hook/relay`, `/pubkey`, `/qr`, `/health`, `/fcm/register`
- `app/hub.py` — Central `Hub` singleton: room management (one desktop + N mobiles per user), message routing, offline queue delivery, approval futures, E2EE key registration
- `app/database.py` — SQLite persistence: `offline_queue`, `sessions`, `refresh_tokens`, `public_keys`, `user_preferences`
- `app/auth.py` — JWT (HS256) generation/verification, pairing key bypass, single-use refresh tokens
- `app/models.py` — Pydantic models for protocol types + dataclasses for internal state
- `app/utils/e2ee.py` — Key validation (65-byte uncompressed P-256 check, SHA-256 fingerprint)
- `app/utils/hooks.py` — Extracts session metadata from unencrypted envelopes (cwd, status, session start/end)
- `app/utils/errors.py` — Error response helpers with codes: `INVALID_ENVELOPE`, `AUTH_FAILED`, `ROOM_NOT_FOUND`, etc.
- `app/utils/qr_utils.py` — QR code generation: SVG HTML page + terminal ASCII output + startup banner
- `app/config.py` — All settings from env vars with sensible defaults

**Key design decisions:**
- Mobile and desktop share the same `user_id` ("desktop") for message routing
- Offline queue persists undelivered messages to SQLite; delivered when mobile connects
- WebSocket sends use per-connection `asyncio.Queue` + background sender task to decouple HTTP producers from WS consumers
- Approval futures let the relay hold a tool permission request until mobile responds (with configurable timeout + fallback)

### Plugin (`cli-notify-plugin/` — Claude Code plugin)

- `.claude-plugin/plugin.json` — Plugin manifest
- `hooks/hooks.json` — 8 Claude Code lifecycle hooks, all invoking `scripts/relay-forward.py`
- `scripts/relay-forward.py` — Single-file Python implementation: reads hook data from stdin, maps hook name to event type, enriches data, optionally E2EE encrypts, POSTs to relay with retry (exponential backoff + jitter, max 3 attempts)

The plugin is **non-blocking** — failures never block Claude Code. All relay communication is best-effort.

### Tests (`tests/`)

- `tests/protocol/schema-validation.test.js` — Validates envelopes against the schema (zero external deps, inline validator)
- `tests/plugin/test_relay_forward.py` — Tests plugin hook processing, envelope building, and E2EE encryption
- `tests/fixtures/` — `valid-envelopes.json` (17 valid), `invalid-envelopes.json` (10 invalid) for schema tests

Hub tests use async wrappers (`asyncio.to_thread`) to avoid blocking the event loop during SQLite operations.

## Key Conventions

- **Protocol is source of truth**: When changing message formats, update `protocol/schema.json` first, then update fixtures and all three components to match
- **Relay is a blind forwarder**: Never add decryption to the relay; E2EE data passes through unmodified
- **Plugin is best-effort**: Never throw from plugin code — catch and log errors, never block Claude Code
- **Node.js ESM** in tests (`"type": "module"`)
- **Python 3.10+** for relay (FastAPI + uvicorn) and plugin (httpx + cryptography)
- Global `hub` singleton in `cloud-relay/app/hub.py` is used by all modules via import
