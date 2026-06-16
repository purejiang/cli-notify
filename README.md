# CLI-Notify

Real-time [Claude Code](https://claude.ai/code) session mirroring and remote control on Android.

Mirror Claude Code desktop sessions to your phone in real-time — view messages, approve/deny tool permissions, and send replies.

## Architecture

```
Claude Code Desktop ──HTTP hooks──▶ Cloud Relay (VPS) ──WebSocket──▶ Android App
```

## Components

| Directory | Description |
|---|---|
| [`android/`](https://github.com/purejiang/cli-notify-android) | Android app (Kotlin + Jetpack Compose) |
| [`cloud-relay/`](https://github.com/purejiang/cli-notify-relay) | Cloud relay server (Python + FastAPI) |
| [`cli-notify-plugin/`](https://github.com/purejiang/cli-notify-plugin) | Claude Code plugin (hooks config only) |

## Quick Start

### 1. Deploy Cloud Relay

```bash
cd cloud-relay
docker-compose up -d
```

The relay prints a QR code and Pairing Key — save these.

### 2. Install Plugin

Copy `cli-notify-plugin/` to your project, then:

```bash
claude --plugin-dir ./cli-notify-plugin
```

In the Claude Code session, run `/cli-notify:setup <pairing-key>` to configure.

### 3. Connect Android App

Build and install the Android app, scan the QR code from the relay, or manually enter the WebSocket URL and token.

## Cloning

```bash
git clone --recurse-submodules https://github.com/purejiang/cli-notify.git
```
