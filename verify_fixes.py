"""Verify all Stage A/B/C/D fixes are applied correctly."""
import sys
import os

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")

# ── Stage A checks ──
print("\n=== Stage A: Stability ===")

# main.py: BaseException catch
with open("cloud-relay/main.py", "r", encoding="utf-8") as f:
    main_py = f.read()
check("main.py uses BaseException (not just WebSocketDisconnect)",
      "except BaseException:" in main_py)
check("main.py sender_task await BaseException",
      "except BaseException:" in main_py.split("sender_task.cancel()")[1] if "sender_task.cancel()" in main_py else False)
check("main.py WindowsSelectorEventLoopPolicy",
      "WindowsSelectorEventLoopPolicy" in main_py)
check("main.py exception_handler installed",
      "loop.set_exception_handler(_exception_handler)" in main_py)
check("main.py ws_ping_interval passed",
      "ws_ping_interval=settings.ws_ping_interval" in main_py)

# hub.py: sender_loop exception handling
with open("cloud-relay/app/hub.py", "r", encoding="utf-8") as f:
    hub_py = f.read()
check("hub.py _sender_loop has except Exception",
      "except Exception:\n" in hub_py.split("except (asyncio.CancelledError")[1] if "asyncio.CancelledError" in hub_py else False)
check("hub.py async DB wrappers exist",
      "_db_dequeue" in hub_py and "_db_enqueue" in hub_py and "_db_mark_delivered" in hub_py)
check("hub.py _deliver_queue uses _db_dequeue",
      "await _db_dequeue(user_id)" in hub_py)
check("hub.py register_public_key is async",
      "async def register_public_key" in hub_py)
check("hub.py Queue has maxsize",
      "Queue(maxsize=256)" in hub_py)
check("hub.py _send_to handles QueueFull",
      "QueueFull" in hub_py)
check("hub.py _enqueue_offline fire-and-forget",
      "asyncio.ensure_future(_db_enqueue(user_id, message))" in hub_py)

# requirements.txt
with open("cloud-relay/requirements.txt", "r", encoding="utf-8") as f:
    req = f.read()
check("requirements.txt pin websockets",
      "websockets>=12.0,<15.0" in req)
check("requirements.txt pin uvicorn",
      "uvicorn[standard]>=0.29.0,<0.35.0" in req)

# ── Stage B checks ──
print("\n=== Stage B: Security ===")

with open("cloud-relay/app/routers.py", "r", encoding="utf-8") as f:
    routes_py = f.read()
check("routes.py uses secrets.compare_digest in auth_login",
      "secrets.compare_digest(secret, settings.pairing_key)" in routes_py)
check("routes.py expires_in not hardcoded",
      "settings.jwt_expiry_seconds" in routes_py)
check("routes.py import secrets",
      "import secrets" in routes_py)

with open("cloud-relay/app/auth.py", "r", encoding="utf-8") as f:
    auth_py = f.read()
check("auth.py uses secrets.compare_digest",
      "secrets.compare_digest(token, settings.pairing_key)" in auth_py)

check("main.py jwt_secret warning",
      "WARNING: Using default JWT_SECRET" in main_py)

# ── Stage C checks ──
print("\n=== Stage C: Approval Loop ===")

check("routes.py has approval hold logic",
      "create_approval_future" in routes_py)
check("routes.py timeout fallback deny",
      'decision = "deny"' in routes_py and "Time" in routes_py)
check("hub.py _handle_message has tool.permission_response",
      "tool.permission_response" in hub_py)
check("hub.py resolve_approval called",
      "self.resolve_approval(user_id, correlation_id, decision)" in hub_py)

# plugin
with open("cli-notify-plugin/scripts/relay-forward.py", "r", encoding="utf-8") as f:
    plugin_py = f.read()
check("plugin post_to_relay returns Optional[Dict]",
      "Optional[Dict[str, Any]]" in plugin_py)
check("plugin PreToolUse msgType=request",
      '"request" if is_tool_request else "event"' in plugin_py)
check("plugin reads relay_resp permissionDecision",
      'relay_resp.get("permissionDecision"' in plugin_py)

# schema
with open("protocol/schema.json", "r", encoding="utf-8") as f:
    schema = f.read()
check("schema.json has tool.permission_response",
      "tool.permission_response" in schema)

# ── Stage D checks ──
print("\n=== Stage D: Correctness ===")

check("plugin no sys.exit(1) in main",
      "sys.exit(1)" not in plugin_py.split("if __name__")[0])
check("hub.py get_preferences sends camelCase",
      '"approvalTimeoutMs"' in hub_py and '"fallbackAction"' in hub_py)
check("hub.py _sync_preferences sends camelCase",
      hub_py.count('"approvalTimeoutMs"') >= 2)

# ── Summary ──
print(f"\n{'='*60}")
print(f"Passed: {passed}/{passed+failed}")
if failed > 0:
    print(f"Failed: {failed}")
    sys.exit(1)
else:
    print("All checks passed!")
    sys.exit(0)
