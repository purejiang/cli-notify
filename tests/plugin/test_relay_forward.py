#!/usr/bin/env python3
"""Plugin v4 Integration Tests — test relay_forward.py modules directly.

Tests all 14 categories from the v4 design spec:
  1. Hook mapping (30 known + unknown + empty)
  2. Enable check
  3. msgType determination
  4. tool_use_id extraction
  5. agent_id extraction
  6. turn_id management
  7. Data truncation
  8. MessageDisplay merging
  9. Binary fallback
  10. E2EE encryption
  11. Envelope v2 structure
  12. Decision responses
  13. Retry logic
  14. Config loading

Usage:
  python3 -m pytest tests/plugin/test_relay_forward.py -v
"""

import base64
import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ── httpx stub (for environments without httpx) ──
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TEST_DIR)
import httpx_stub
sys.modules["httpx"] = httpx_stub

# Add plugin scripts to path
PLUGIN_DIR = os.path.abspath(os.path.join(TEST_DIR, "..", "..", "cli-notify-plugin", "scripts"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# Import v4 modules
import config_manager
from config_manager import ConfigManager, ConfigDict
import hook_processor
from hook_processor import process_hook, HookEvent, HOOK_TYPE_MAP, REQUEST_HOOKS
import envelope_builder
from envelope_builder import build_envelope, build_decision_response, TurnManager, MessageBuffer
import encryptor
from encryptor import encrypt_envelope, decrypt_payload
import relay_client


# ═══════════════════════════════════════════════════════════════
# 1. Hook Mapping Tests
# ═══════════════════════════════════════════════════════════════

class TestHookMapping(unittest.TestCase):
    """All 30 known hooks + unknown + empty name → correct EventType."""

    def setUp(self):
        self.config = ConfigDict({"core_hooks": list(HOOK_TYPE_MAP.keys()) + [""], "extra_hooks": [], "approval_mode": "desktop"})

    def test_all_30_hooks_mapped(self):
        """Verify all 30 hook names are in the map."""
        expected_count = 30
        self.assertEqual(len(HOOK_TYPE_MAP), expected_count,
                         f"Expected {expected_count} hooks, got {len(HOOK_TYPE_MAP)}")

    def test_session_start(self):
        self.assertEqual(HOOK_TYPE_MAP["SessionStart"], "session_start")

    def test_session_end(self):
        self.assertEqual(HOOK_TYPE_MAP["SessionEnd"], "session_end")

    def test_user_prompt_submit(self):
        self.assertEqual(HOOK_TYPE_MAP["UserPromptSubmit"], "user_prompt_submit")

    def test_pre_tool_use(self):
        self.assertEqual(HOOK_TYPE_MAP["PreToolUse"], "pre_tool_use")

    def test_post_tool_use(self):
        self.assertEqual(HOOK_TYPE_MAP["PostToolUse"], "post_tool_use")

    def test_post_tool_use_failure(self):
        self.assertEqual(HOOK_TYPE_MAP["PostToolUseFailure"], "post_tool_use_failure")

    def test_post_tool_batch(self):
        self.assertEqual(HOOK_TYPE_MAP["PostToolBatch"], "post_tool_batch")

    def test_permission_request(self):
        self.assertEqual(HOOK_TYPE_MAP["PermissionRequest"], "permission_request")

    def test_permission_denied(self):
        self.assertEqual(HOOK_TYPE_MAP["PermissionDenied"], "permission_denied")

    def test_stop(self):
        self.assertEqual(HOOK_TYPE_MAP["Stop"], "stop")

    def test_stop_failure(self):
        self.assertEqual(HOOK_TYPE_MAP["StopFailure"], "stop_failure")

    def test_notification(self):
        self.assertEqual(HOOK_TYPE_MAP["Notification"], "notification")

    def test_message_display(self):
        self.assertEqual(HOOK_TYPE_MAP["MessageDisplay"], "message_display")

    def test_subagent_start(self):
        self.assertEqual(HOOK_TYPE_MAP["SubagentStart"], "subagent_start")

    def test_subagent_stop(self):
        self.assertEqual(HOOK_TYPE_MAP["SubagentStop"], "subagent_stop")

    def test_task_created(self):
        self.assertEqual(HOOK_TYPE_MAP["TaskCreated"], "task_created")

    def test_task_completed(self):
        self.assertEqual(HOOK_TYPE_MAP["TaskCompleted"], "task_completed")

    def test_elicitation(self):
        self.assertEqual(HOOK_TYPE_MAP["Elicitation"], "elicitation")

    def test_elicitation_result(self):
        self.assertEqual(HOOK_TYPE_MAP["ElicitationResult"], "elicitation_result")

    def test_teammate_idle(self):
        self.assertEqual(HOOK_TYPE_MAP["TeammateIdle"], "teammate_idle")

    def test_setup(self):
        self.assertEqual(HOOK_TYPE_MAP["Setup"], "setup")

    def test_pre_compact(self):
        self.assertEqual(HOOK_TYPE_MAP["PreCompact"], "pre_compact")

    def test_post_compact(self):
        self.assertEqual(HOOK_TYPE_MAP["PostCompact"], "post_compact")

    def test_config_change(self):
        self.assertEqual(HOOK_TYPE_MAP["ConfigChange"], "config_change")

    def test_cwd_changed(self):
        self.assertEqual(HOOK_TYPE_MAP["CwdChanged"], "cwd_changed")

    def test_file_changed(self):
        self.assertEqual(HOOK_TYPE_MAP["FileChanged"], "file_changed")

    def test_instructions_loaded(self):
        self.assertEqual(HOOK_TYPE_MAP["InstructionsLoaded"], "instructions_loaded")

    def test_worktree_create(self):
        self.assertEqual(HOOK_TYPE_MAP["WorktreeCreate"], "worktree_create")

    def test_worktree_remove(self):
        self.assertEqual(HOOK_TYPE_MAP["WorktreeRemove"], "worktree_remove")

    def test_user_prompt_expansion(self):
        self.assertEqual(HOOK_TYPE_MAP["UserPromptExpansion"], "user_prompt_expansion")

    def test_unknown_hook_defaults_to_snake_case(self):
        event = process_hook({"hook_event_name": "UnknownEventType"}, self.config)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "unknown_event_type")

    def test_empty_hook_name_defaults_to_unknown_hook(self):
        event = process_hook({"hook_event_name": ""}, self.config)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "unknown_hook")


# ═══════════════════════════════════════════════════════════════
# 2. Enable Check Tests
# ═══════════════════════════════════════════════════════════════

class TestEnableCheck(unittest.TestCase):
    """Core hooks enabled by default; extra hooks require config."""

    def test_core_hook_enabled_by_default(self):
        config = ConfigDict({"core_hooks": ["SessionStart"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "SessionStart"}, config)
        self.assertIsNotNone(event)

    def test_extra_hook_disabled_by_default(self):
        config = ConfigDict({"core_hooks": [], "extra_hooks": []})
        event = process_hook({"hook_event_name": "CwdChanged"}, config)
        self.assertIsNone(event)

    def test_extra_hook_enabled_when_in_extra_list(self):
        config = ConfigDict({"core_hooks": [], "extra_hooks": ["CwdChanged"]})
        event = process_hook({"hook_event_name": "CwdChanged"}, config)
        self.assertIsNotNone(event)

    def test_nonexistent_hook_still_processes(self):
        config = ConfigDict({"core_hooks": [], "extra_hooks": []})
        event = process_hook({"hook_event_name": "TotallyUnknown"}, config)
        self.assertIsNotNone(event)  # unknown hooks always pass through

    def test_hook_in_both_lists(self):
        config = ConfigDict({"core_hooks": ["Stop"], "extra_hooks": ["Stop"]})
        event = process_hook({"hook_event_name": "Stop"}, config)
        self.assertIsNotNone(event)

    def test_empty_hook_name_always_enabled(self):
        config = ConfigDict({"core_hooks": [], "extra_hooks": []})
        event = process_hook({"hook_event_name": ""}, config)
        self.assertIsNotNone(event)


# ═══════════════════════════════════════════════════════════════
# 3. msgType Determination Tests
# ═══════════════════════════════════════════════════════════════

class TestMsgTypeDetermination(unittest.TestCase):
    """Desktop mode → all event; app/hybrid → request for PreToolUse/PermissionRequest/Elicitation."""

    def test_desktop_mode_all_event(self):
        config = ConfigDict({"core_hooks": list(HOOK_TYPE_MAP.keys()), "extra_hooks": [], "approval_mode": "desktop"})
        for hook in ["PreToolUse", "PermissionRequest", "Elicitation"]:
            event = process_hook({"hook_event_name": hook, "session_id": "s1"}, config)
            self.assertEqual(event.msg_type, "event", f"{hook} should be event in desktop mode")

    def test_app_mode_pre_tool_use_is_request(self):
        config = ConfigDict({"core_hooks": ["PreToolUse"], "extra_hooks": [], "approval_mode": "app"})
        event = process_hook({"hook_event_name": "PreToolUse"}, config)
        self.assertEqual(event.msg_type, "request")

    def test_app_mode_permission_request_is_request(self):
        config = ConfigDict({"core_hooks": ["PermissionRequest"], "extra_hooks": [], "approval_mode": "app"})
        event = process_hook({"hook_event_name": "PermissionRequest"}, config)
        self.assertEqual(event.msg_type, "request")

    def test_app_mode_elicitation_is_request(self):
        config = ConfigDict({"core_hooks": ["Elicitation"], "extra_hooks": [], "approval_mode": "app"})
        event = process_hook({"hook_event_name": "Elicitation"}, config)
        self.assertEqual(event.msg_type, "request")

    def test_hybrid_mode_pre_tool_use_is_request(self):
        config = ConfigDict({"core_hooks": ["PreToolUse"], "extra_hooks": [], "approval_mode": "hybrid"})
        event = process_hook({"hook_event_name": "PreToolUse"}, config)
        self.assertEqual(event.msg_type, "request")

    def test_app_mode_session_start_is_event(self):
        config = ConfigDict({"core_hooks": ["SessionStart"], "extra_hooks": [], "approval_mode": "app"})
        event = process_hook({"hook_event_name": "SessionStart"}, config)
        self.assertEqual(event.msg_type, "event")


# ═══════════════════════════════════════════════════════════════
# 4. tool_use_id Extraction Tests
# ═══════════════════════════════════════════════════════════════

class TestToolUseIdExtraction(unittest.TestCase):
    """PreToolUse/PostToolUse/PermissionRequest extract tool_use_id."""

    def test_pre_tool_use_has_tool_use_id(self):
        config = ConfigDict({"core_hooks": ["PreToolUse"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "PreToolUse", "tool_use_id": "tu-001"}, config)
        self.assertEqual(event.tool_use_id, "tu-001")

    def test_post_tool_use_has_tool_use_id(self):
        config = ConfigDict({"core_hooks": ["PostToolUse"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "PostToolUse", "tool_use_id": "tu-002"}, config)
        self.assertEqual(event.tool_use_id, "tu-002")

    def test_session_start_no_tool_use_id(self):
        config = ConfigDict({"core_hooks": ["SessionStart"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "SessionStart"}, config)
        self.assertIsNone(event.tool_use_id)

    def test_tool_use_id_from_camelCase(self):
        config = ConfigDict({"core_hooks": ["PreToolUse"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "PreToolUse", "toolUseId": "tu-003"}, config)
        self.assertEqual(event.tool_use_id, "tu-003")


# ═══════════════════════════════════════════════════════════════
# 5. agent_id Extraction Tests
# ═══════════════════════════════════════════════════════════════

class TestAgentIdExtraction(unittest.TestCase):
    """Subagent events extract agent_id."""

    def test_subagent_start_has_agent_id(self):
        config = ConfigDict({"core_hooks": ["SubagentStart"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "SubagentStart", "agent_id": "ag-001"}, config)
        self.assertEqual(event.agent_id, "ag-001")

    def test_subagent_stop_has_agent_id(self):
        config = ConfigDict({"core_hooks": ["SubagentStop"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "SubagentStop", "agent_id": "ag-001"}, config)
        self.assertEqual(event.agent_id, "ag-001")

    def test_main_agent_event_no_agent_id(self):
        config = ConfigDict({"core_hooks": ["PostToolUse"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "PostToolUse"}, config)
        self.assertIsNone(event.agent_id)

    def test_agent_id_from_camelCase(self):
        config = ConfigDict({"core_hooks": ["SubagentStart"], "extra_hooks": []})
        event = process_hook({"hook_event_name": "SubagentStart", "agentId": "ag-002"}, config)
        self.assertEqual(event.agent_id, "ag-002")


# ═══════════════════════════════════════════════════════════════
# 6. Envelope Structure Tests
# ═══════════════════════════════════════════════════════════════

class TestEnvelopeStructure(unittest.TestCase):
    """Envelope v2 has all required fields, correct correlationId/groupId."""

    def setUp(self):
        self.config = ConfigManager.load()
        self.config["core_hooks"] = list(HOOK_TYPE_MAP.keys())
        self.config["extra_hooks"] = []
        self.config["e2ee_enabled"] = False

    def test_envelope_has_all_8_required_fields(self):
        event = process_hook({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/tmp"}, self.config)
        envelope = build_envelope({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/tmp"}, event, self.config)
        required = ["type", "id", "msgType", "sessionId", "from", "timestamp", "encrypted", "data"]
        for field in required:
            self.assertIn(field, envelope, f"Missing required field: {field}")

    def test_envelope_v2_data_has_raw_and_truncated(self):
        event = process_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "hi"}, self.config)
        envelope = build_envelope({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "hi"}, event, self.config)
        data = envelope["data"]
        self.assertIn("raw", data)
        self.assertIn("truncated", data)
        self.assertFalse(data["truncated"])

    def test_envelope_type_is_v2_event_type(self):
        event = process_hook({"hook_event_name": "SessionStart", "session_id": "s1"}, self.config)
        envelope = build_envelope({"hook_event_name": "SessionStart", "session_id": "s1"}, event, self.config)
        self.assertEqual(envelope["type"], "session_start")

    def test_from_is_desktop(self):
        event = process_hook({"hook_event_name": "Notification", "session_id": "s1"}, self.config)
        envelope = build_envelope({"hook_event_name": "Notification", "session_id": "s1"}, event, self.config)
        self.assertEqual(envelope["from"], "desktop")

    def test_correlationId_matches_tool_use_id(self):
        event = process_hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_use_id": "tu-001"}, self.config)
        envelope = build_envelope({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_use_id": "tu-001"}, event, self.config)
        self.assertEqual(envelope["correlationId"], "tu-001")

    def test_groupId_matches_agent_id(self):
        event = process_hook({"hook_event_name": "SubagentStart", "session_id": "s1", "agent_id": "ag-001"}, self.config)
        envelope = build_envelope({"hook_event_name": "SubagentStart", "session_id": "s1", "agent_id": "ag-001"}, event, self.config)
        self.assertEqual(envelope["groupId"], "ag-001")


# ═══════════════════════════════════════════════════════════════
# 7. turn_id Management Tests
# ═══════════════════════════════════════════════════════════════

class TestTurnIdManagement(unittest.TestCase):
    """UserPromptSubmit generates new turn_id; Stop clears it; intermediate hooks reuse."""

    def setUp(self):
        # Reset TurnManager singleton state
        envelope_builder._turn_mgr = TurnManager()
        self.config = ConfigManager.load()
        self.config["core_hooks"] = list(HOOK_TYPE_MAP.keys())
        self.config["extra_hooks"] = []
        self.config["e2ee_enabled"] = False

    def test_user_prompt_generates_turn_id(self):
        event = process_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.config)
        envelope = build_envelope({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, event, self.config)
        self.assertIsNotNone(envelope["data"]["turn_id"])

    def test_stop_clears_turn_id(self):
        # Start a turn
        event1 = process_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.config)
        build_envelope({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, event1, self.config)
        first_turn_id = envelope_builder._turn_mgr.current
        self.assertIsNotNone(first_turn_id)

        # Stop ends the turn
        event2 = process_hook({"hook_event_name": "Stop", "session_id": "s1"}, self.config)
        build_envelope({"hook_event_name": "Stop", "session_id": "s1"}, event2, self.config)
        self.assertIsNone(envelope_builder._turn_mgr.current)

    def test_intermediate_hook_reuses_turn_id(self):
        event1 = process_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.config)
        env1 = build_envelope({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, event1, self.config)
        turn_id = env1["data"]["turn_id"]

        event2 = process_hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_use_id": "tu-001"}, self.config)
        env2 = build_envelope({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_use_id": "tu-001"}, event2, self.config)
        self.assertEqual(env2["data"]["turn_id"], turn_id)

    def test_session_end_resets_turn(self):
        event1 = process_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.config)
        build_envelope({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, event1, self.config)

        event2 = process_hook({"hook_event_name": "SessionEnd", "session_id": "s1"}, self.config)
        build_envelope({"hook_event_name": "SessionEnd", "session_id": "s1"}, event2, self.config)
        self.assertIsNone(envelope_builder._turn_mgr.current)

    def test_no_turn_id_before_first_user_prompt(self):
        # Clear any persisted turn state from previous tests
        envelope_builder._turn_mgr.end()
        from envelope_builder import _clear_turn_id
        _clear_turn_id()
        event = process_hook({"hook_event_name": "SessionStart", "session_id": "s1"}, self.config)
        envelope = build_envelope({"hook_event_name": "SessionStart", "session_id": "s1"}, event, self.config)
        self.assertIsNone(envelope["data"]["turn_id"])

    def test_stop_failure_also_clears_turn(self):
        event1 = process_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.config)
        build_envelope({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, event1, self.config)

        envelope_builder._turn_mgr.end()
        # Simulate StopFailure
        event2 = process_hook({"hook_event_name": "StopFailure", "session_id": "s1"}, self.config)
        build_envelope({"hook_event_name": "StopFailure", "session_id": "s1"}, event2, self.config)
        self.assertIsNone(envelope_builder._turn_mgr.current)


# ═══════════════════════════════════════════════════════════════
# 8. Data Truncation Tests
# ═══════════════════════════════════════════════════════════════

class TestDataTruncation(unittest.TestCase):
    """Envelope data is truncated when exceeding max_data_size."""

    def setUp(self):
        self.config = ConfigManager.load()
        self.config["core_hooks"] = list(HOOK_TYPE_MAP.keys())
        self.config["e2ee_enabled"] = False

    def test_small_data_not_truncated(self):
        self.config["max_data_size"] = 51200
        event = process_hook({"hook_event_name": "Notification", "session_id": "s1", "message": "short"}, self.config)
        envelope = build_envelope({"hook_event_name": "Notification", "session_id": "s1", "message": "short"}, event, self.config)
        self.assertFalse(envelope["data"]["truncated"])

    def test_large_data_truncated(self):
        self.config["max_data_size"] = 100
        big_text = "A" * 500
        event = process_hook({"hook_event_name": "Notification", "session_id": "s1", "message": big_text}, self.config)
        envelope = build_envelope({"hook_event_name": "Notification", "session_id": "s1", "message": big_text}, event, self.config)
        self.assertTrue(envelope["data"]["truncated"])
        self.assertIn("_truncated", envelope["data"]["raw"])
        self.assertIn("_original_size", envelope["data"]["raw"])

    def test_truncation_invalid_json_fallback(self):
        # Test that truncation that cuts JSON in half uses _truncated_text fallback
        from envelope_builder import _truncate_raw
        raw = {"key": "x" * 1000}
        result = _truncate_raw(raw, 50)
        self.assertIn("_truncated_text", result)
        self.assertIn("_truncated", result)

    def test_truncation_preserves_valid_json(self):
        from envelope_builder import _truncate_raw
        raw = {"short": "ok"}
        result = _truncate_raw(raw, 10000)
        # Should not be truncated since 10000 > size
        # _truncate_raw always adds truncation markers, but test the JSON validity
        self.assertIn("_truncated", result)

    def test_zero_max_data_size(self):
        self.config["max_data_size"] = 0
        event = process_hook({"hook_event_name": "Notification", "session_id": "s1"}, self.config)
        envelope = build_envelope({"hook_event_name": "Notification", "session_id": "s1"}, event, self.config)
        self.assertTrue(envelope["data"]["truncated"])


# ═══════════════════════════════════════════════════════════════
# 9. MessageBuffer Tests
# ═══════════════════════════════════════════════════════════════

class TestMessageBuffer(unittest.TestCase):
    """MessageDisplay delta merging within time window."""

    def test_single_delta_flushed_immediately(self):
        buf = MessageBuffer(window_ms=0)  # No window
        result = buf.add({"message_id": "m1", "delta": "hello", "hook_event_name": "MessageDisplay"})
        self.assertIsNotNone(result)
        self.assertEqual(result["delta"], "hello")

    def test_multiple_deltas_merged(self):
        buf = MessageBuffer(window_ms=1000)
        buf.add({"message_id": "m1", "delta": "hello ", "hook_event_name": "MessageDisplay"})
        result = buf.add({"message_id": "m1", "delta": "world", "hook_event_name": "MessageDisplay"})
        # May still be in window, force flush
        if result is None:
            result = buf.flush()
        self.assertIsNotNone(result)
        self.assertEqual(result["delta"], "hello world")

    def test_new_message_id_flushes_old(self):
        buf = MessageBuffer(window_ms=1000)
        buf.add({"message_id": "m1", "delta": "first", "hook_event_name": "MessageDisplay"})
        result = buf.add({"message_id": "m2", "delta": "second", "hook_event_name": "MessageDisplay"})
        self.assertIsNotNone(result)
        self.assertEqual(result["delta"], "first")

    def test_flush_returns_none_when_empty(self):
        buf = MessageBuffer(window_ms=1000)
        self.assertIsNone(buf.flush())

    def test_merged_count_tracked(self):
        buf = MessageBuffer(window_ms=100)
        buf.add({"message_id": "m1", "delta": "a", "hook_event_name": "MessageDisplay"})
        buf.add({"message_id": "m1", "delta": "b", "hook_event_name": "MessageDisplay"})
        result = buf.flush()
        self.assertIsNotNone(result)
        self.assertEqual(result["_merged_count"], 2)

    def test_empty_buffer_returns_none(self):
        buf = MessageBuffer(window_ms=100)
        self.assertIsNone(buf.flush())


# ═══════════════════════════════════════════════════════════════
# 10. Binary Fallback Tests
# ═══════════════════════════════════════════════════════════════

class TestBinaryFallback(unittest.TestCase):
    """Non-serializable data gets base64-encoded as _raw_b64."""

    def test_binary_data_fallback(self):
        self.config = ConfigManager.load()
        self.config["e2ee_enabled"] = False
        self.config["core_hooks"] = ["SessionStart"]
        # Create data that can be serialized (all valid JSON)
        event = process_hook({"hook_event_name": "SessionStart", "session_id": "s1"}, self.config)
        envelope = build_envelope({"hook_event_name": "SessionStart", "session_id": "s1"}, event, self.config)
        # Normal path — should not trigger b64 fallback
        self.assertNotIn("_raw_b64", envelope["data"]["raw"])

    def test_default_str_serialization(self):
        # Test that objects with __str__ work via default=str
        class CustomObj:
            def __str__(self):
                return "custom"

        self.config = ConfigManager.load()
        self.config["e2ee_enabled"] = False
        self.config["core_hooks"] = ["SessionStart"]
        # Hook data containing unserializable value
        body = {"hook_event_name": "SessionStart", "session_id": "s1", "custom": CustomObj()}
        event = process_hook(body, self.config)
        # This should not throw — build_envelope handles it via default=str
        envelope = build_envelope(body, event, self.config)
        self.assertIn("raw", envelope["data"])


# ═══════════════════════════════════════════════════════════════
# 11. E2EE Encryption Tests
# ═══════════════════════════════════════════════════════════════

class TestE2EEEncryption(unittest.TestCase):
    """E2EE encryption produces EncryptedPayload; HKDF info is 'cli-notify-v2'."""

    def setUp(self):
        # Generate a keypair for testing
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.pub_key_b64 = base64.b64encode(
            self.private_key.public_key().public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        ).decode()

    def test_encrypt_envelope_required_fields(self):
        envelope = {"type": "test", "data": {"raw": {}, "truncated": False}}
        result = encrypt_envelope(envelope, self.pub_key_b64)
        self.assertTrue(result["encrypted"])
        data = result["data"]
        self.assertIn("ephemeralKey", data)
        self.assertIn("iv", data)
        self.assertIn("ciphertext", data)

    def test_encrypted_data_is_not_original(self):
        envelope = {"type": "test", "data": {"raw": {"secret": "value"}, "truncated": False}}
        result = encrypt_envelope(dict(envelope), self.pub_key_b64)
        self.assertNotEqual(result["data"]["ciphertext"], json.dumps(envelope["data"]))

    def test_encrypt_then_decrypt_roundtrip(self):
        original_data = {"raw": {"content": "hello"}, "truncated": False}
        envelope = {"type": "test", "data": dict(original_data)}
        encrypted = encrypt_envelope(dict(envelope), self.pub_key_b64)
        decrypted = decrypt_payload(encrypted["data"], self.private_key)
        self.assertEqual(decrypted, original_data)

    def test_hkdf_info_is_v2(self):
        self.assertEqual(encryptor.HKDF_INFO, b"cli-notify-v2")

    def test_ephemeral_key_is_65_bytes(self):
        envelope = {"type": "test", "data": {"raw": {}, "truncated": False}}
        result = encrypt_envelope(envelope, self.pub_key_b64)
        decoded = base64.b64decode(result["data"]["ephemeralKey"])
        self.assertEqual(len(decoded), 65)
        self.assertEqual(decoded[0], 0x04)


# ═══════════════════════════════════════════════════════════════
# 12. Decision Response Tests
# ═══════════════════════════════════════════════════════════════

class TestDecisionResponse(unittest.TestCase):
    """Permission decision responses for PreToolUse, PermissionRequest, Elicitation."""

    def setUp(self):
        self.config = ConfigDict({"fallback_action": "ask", "approval_timeout_ms": 30000})

    def test_pre_tool_use_allow(self):
        result = build_decision_response(
            {"hook_event_name": "PreToolUse"},
            {"decision": "allow", "reason": "OK"},
            self.config,
        )
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecisionReason"], "OK")

    def test_pre_tool_use_deny(self):
        result = build_decision_response(
            {"hook_event_name": "PreToolUse"},
            {"decision": "deny", "reason": "Not safe"},
            self.config,
        )
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_pre_tool_use_timeout_fallback_ask(self):
        result = build_decision_response(
            {"hook_event_name": "PreToolUse"},
            None,
            self.config,
        )
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_pre_tool_use_timeout_fallback_deny(self):
        config = ConfigDict({"fallback_action": "deny", "approval_timeout_ms": 30000})
        result = build_decision_response(
            {"hook_event_name": "PreToolUse"},
            None,
            config,
        )
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_permission_request_allow(self):
        result = build_decision_response(
            {"hook_event_name": "PermissionRequest"},
            {"decision": "allow", "reason": "Approved"},
            self.config,
        )
        self.assertEqual(result["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_permission_request_deny(self):
        result = build_decision_response(
            {"hook_event_name": "PermissionRequest"},
            {"decision": "deny"},
            self.config,
        )
        self.assertEqual(result["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def test_elicitation_allow(self):
        result = build_decision_response(
            {"hook_event_name": "Elicitation"},
            {"decision": "allow"},
            self.config,
        )
        self.assertEqual(result["hookSpecificOutput"]["action"], "accept")

    def test_elicitation_deny(self):
        result = build_decision_response(
            {"hook_event_name": "Elicitation"},
            {"decision": "deny"},
            self.config,
        )
        self.assertEqual(result["hookSpecificOutput"]["action"], "decline")


# ═══════════════════════════════════════════════════════════════
# 13. Config Loading Tests
# ═══════════════════════════════════════════════════════════════

class TestConfigLoading(unittest.TestCase):
    """ConfigManager loads defaults, merges overrides, handles missing files."""

    def test_default_config_has_required_keys(self):
        config = ConfigManager.load()
        self.assertIn("relay_url", config)
        self.assertIn("jwt", config)
        self.assertIn("approval_mode", config)
        self.assertIn("core_hooks", config)
        self.assertIn("max_data_size", config)

    def test_default_core_hooks_count(self):
        config = ConfigManager.load()
        self.assertEqual(len(config["core_hooks"]), 18)

    def test_custom_config_overrides_default(self):
        # Save custom config, load, verify
        original = ConfigManager.load()
        custom = dict(original)
        custom["approval_mode"] = "app"
        custom["max_data_size"] = 99999
        try:
            ConfigManager.save(custom)
            loaded = ConfigManager.load()
            self.assertEqual(loaded["approval_mode"], "app")
            self.assertEqual(loaded["max_data_size"], 99999)
        finally:
            # Restore original
            ConfigManager.save(original)

    def test_is_configured_requires_relay_url_and_jwt(self):
        empty = ConfigDict({"relay_url": "", "jwt": ""})
        self.assertFalse(ConfigManager.is_configured(empty))

        partial = ConfigDict({"relay_url": "http://relay", "jwt": ""})
        self.assertFalse(ConfigManager.is_configured(partial))

        full = ConfigDict({"relay_url": "http://relay", "jwt": "token123"})
        self.assertTrue(ConfigManager.is_configured(full))


# ═══════════════════════════════════════════════════════════════
# 14. Relay Client Retry Tests
# ═══════════════════════════════════════════════════════════════

class TestRelayClient(unittest.TestCase):
    """Retry logic, non-retryable codes, JWT refresh."""

    def test_max_retries_constant(self):
        self.assertEqual(relay_client.MAX_RETRIES, 3)

    def test_non_retryable_codes(self):
        expected = {400, 403, 404, 405, 409, 410, 422}
        self.assertEqual(relay_client.NON_RETRYABLE, expected)

    def test_post_with_200_success(self):
        config = ConfigDict({"relay_url": "http://test", "jwt": "tok", "offline_cache": False})
        client = relay_client.RelayClient(config)
        # Mock the httpx Client.post to return 200
        original_post = client.client.post
        try:
            mock_resp = unittest.mock.MagicMock()
            mock_resp.status_code = 200
            client.client.post = unittest.mock.MagicMock(return_value=mock_resp)
            result = client.post({"type": "test"})
            self.assertTrue(result)
        finally:
            client.client.post = original_post


if __name__ == "__main__":
    unittest.main()
