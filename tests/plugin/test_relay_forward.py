#!/usr/bin/env python3
"""Plugin Integration Tests — test relay-forward.py functions directly.

Tests:
  1. Data extraction for all 8 hook event types
  2. Hook event type mapping
  3. Edit line number computation
  4. Idle notification builder
  5. Encryption payload format
  6. Envelope structure

Usage:
  python3 tests/plugin/test_relay_forward.py
"""

import base64
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── httpx stub ─────────────────────────────────────────────────────────────
# relay-forward.py imports httpx, which may not be installed in test envs.
# We inject a stub before importing the module under test.
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TEST_DIR)
import httpx_stub
sys.modules["httpx"] = httpx_stub
# ────────────────────────────────────────────────────────────────────────────

# Add plugin script to path
PLUGIN_DIR = os.path.abspath(os.path.join(TEST_DIR, "..", "..", "cli-notify-plugin", "scripts"))
sys.path.insert(0, PLUGIN_DIR)

# Import the module (relay-forward has no __init__.py, so use importlib)
import importlib.util
spec = importlib.util.spec_from_file_location("relay_forward", os.path.join(PLUGIN_DIR, "relay-forward.py"))
rf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rf)


class TestHookEventMap(unittest.TestCase):
    """Verify HOOK_EVENT_MAP covers all 8 hook types."""

    def test_all_8_hooks_mapped(self):
        expected = {
            "SessionStart": "session.start",
            "UserPromptSubmit": "message.user",
            "PreToolUse": "tool.request",
            "PostToolUse": "tool.result",
            "PermissionRequest": "tool.permission_request",
            "Stop": "message.assistant",
            "SessionEnd": "session.end",
            "Notification": "notification",
        }
        self.assertEqual(rf.HOOK_EVENT_MAP, expected)

    def test_unknown_hook_not_mapped(self):
        self.assertNotIn("UnknownHook", rf.HOOK_EVENT_MAP)


class TestExtractData(unittest.TestCase):
    """Test data extraction for all 8 hook types."""

    def test_session_start(self):
        body = {"hook_event_name": "SessionStart", "cwd": "/home/test"}
        result = rf.extract_data(body)
        self.assertEqual(result.get("cwd"), "/home/test")

    def test_session_start_missing_cwd(self):
        body = {"hook_event_name": "SessionStart"}
        result = rf.extract_data(body)
        self.assertEqual(result.get("cwd"), "")

    def test_user_prompt(self):
        body = {"hook_event_name": "UserPromptSubmit", "prompt": "Hello world"}
        result = rf.extract_data(body)
        self.assertEqual(result.get("content"), "Hello world")

    def test_user_prompt_empty(self):
        body = {"hook_event_name": "UserPromptSubmit", "prompt": ""}
        result = rf.extract_data(body)
        self.assertEqual(result.get("content"), "")

    def test_pre_tool_use(self):
        body = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
        result = rf.extract_data(body)
        self.assertEqual(result.get("toolName"), "Bash")
        self.assertEqual(result.get("params"), {"command": "ls -la"})

    def test_pre_tool_use_no_input(self):
        body = {"hook_event_name": "PreToolUse", "tool_name": "Read"}
        result = rf.extract_data(body)
        self.assertEqual(result.get("params"), {})

    def test_post_tool_use(self):
        body = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_response": {"output": "hello"},
        }
        result = rf.extract_data(body)
        self.assertEqual(result.get("toolName"), "Bash")
        self.assertEqual(result.get("success"), True)

    def test_post_tool_use_edit_line_numbers(self):
        body = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_response": {
                "originalFile": "line1\nline2\nline3\nline4",
                "oldString": "line2\nline3",
            },
        }
        result = rf.extract_data(body)
        self.assertIn("editLineInfo", result)
        self.assertEqual(result["editLineInfo"]["oldLineStart"], 2)
        self.assertEqual(result["editLineInfo"]["oldLineEnd"], 3)

    def test_post_tool_use_edit_no_old_string(self):
        body = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_response": {"originalFile": "line1\nline2"},
        }
        result = rf.extract_data(body)
        self.assertNotIn("editLineInfo", result)

    def test_permission_request(self):
        body = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "curl example.com"},
        }
        result = rf.extract_data(body)
        self.assertEqual(result.get("toolName"), "Bash")
        self.assertIn("message", result)

    def test_stop(self):
        body = {
            "hook_event_name": "Stop",
            "last_assistant_message": "Done!",
        }
        result = rf.extract_data(body)
        self.assertEqual(result.get("content"), "Done!")

    def test_stop_empty_message(self):
        body = {"hook_event_name": "Stop"}
        result = rf.extract_data(body)
        self.assertEqual(result.get("content"), "")

    def test_session_end(self):
        body = {"hook_event_name": "SessionEnd", "reason": "user_exit"}
        result = rf.extract_data(body)
        self.assertEqual(result.get("reason"), "user_exit")

    def test_notification(self):
        body = {
            "hook_event_name": "Notification",
            "notification_type": "permission_prompt",
            "message": "Permission needed",
        }
        result = rf.extract_data(body)
        self.assertEqual(result.get("kind"), "permission_prompt")
        self.assertEqual(result.get("message"), "Permission needed")

    def test_notification_invalid_kind(self):
        body = {
            "hook_event_name": "Notification",
            "notification_type": "invalid_type",
        }
        result = rf.extract_data(body)
        self.assertEqual(result.get("kind"), "idle_prompt")

    def test_unknown_hook(self):
        body = {"hook_event_name": "UnknownEvent"}
        result = rf.extract_data(body)
        self.assertEqual(result, {})


class TestComputeEditLineNumbers(unittest.TestCase):
    """Test Edit tool line number computation."""

    def test_basic(self):
        result = rf.compute_edit_line_numbers({
            "originalFile": "a\nb\nc\nd\ne",
            "oldString": "c\nd",
        })
        self.assertEqual(result["oldLineStart"], 3)
        self.assertEqual(result["oldLineEnd"], 4)

    def test_first_line(self):
        result = rf.compute_edit_line_numbers({
            "originalFile": "hello\nworld",
            "oldString": "hello",
        })
        self.assertEqual(result["oldLineStart"], 1)
        self.assertEqual(result["oldLineEnd"], 1)

    def test_not_found(self):
        result = rf.compute_edit_line_numbers({
            "originalFile": "abc",
            "oldString": "xyz",
        })
        self.assertIsNone(result)

    def test_missing_fields(self):
        result = rf.compute_edit_line_numbers({"originalFile": "abc"})
        self.assertIsNone(result)

    def test_replace_all_flag(self):
        result = rf.compute_edit_line_numbers({
            "originalFile": "a\nb\na\nb",
            "oldString": "a",
            "replaceAll": True,
        })
        self.assertEqual(result["oldLineStart"], 1)
        self.assertTrue(result["replaceAll"])

    def test_non_string_fields(self):
        result = rf.compute_edit_line_numbers({
            "originalFile": 123,
            "oldString": "abc",
        })
        self.assertIsNone(result)


class TestBuildIdleNotification(unittest.TestCase):
    """Test idle notification builder."""

    def test_basic(self):
        result = rf.build_idle_notification("/home/test")
        self.assertEqual(result["kind"], "idle_prompt")
        self.assertIsNone(result["message"])
        self.assertEqual(result["cwd"], "/home/test")

    def test_empty_cwd(self):
        result = rf.build_idle_notification("")
        self.assertEqual(result["cwd"], "")


class TestEncryptPayload(unittest.TestCase):
    """Test E2EE encryption format.

    We verify the output structure and format constraints.
    Actual decryption is tested end-to-end with the Android module.
    """

    def setUp(self):
        # Generate a keypair for testing
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        private_key = ec.generate_private_key(ec.SECP256R1())
        self.phone_pub_key_b64 = base64.b64encode(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        ).decode()

    def test_encrypts_and_returns_required_fields(self):
        data = {"content": "hello"}
        result = rf.encrypt_payload(data, self.phone_pub_key_b64)
        self.assertIn("ephemeralKey", result)
        self.assertIn("iv", result)
        self.assertIn("ciphertext", result)

    def test_ephemeral_key_is_65_bytes_base64(self):
        data = {"test": True}
        result = rf.encrypt_payload(data, self.phone_pub_key_b64)
        decoded = base64.b64decode(result["ephemeralKey"])
        self.assertEqual(len(decoded), 65)
        self.assertEqual(decoded[0], 0x04)  # uncompressed point prefix

    def test_iv_is_12_bytes_base64(self):
        data = {"test": True}
        result = rf.encrypt_payload(data, self.phone_pub_key_b64)
        decoded = base64.b64decode(result["iv"])
        self.assertEqual(len(decoded), 12)

    def test_ciphertext_is_different_each_call(self):
        data = {"content": "same data"}
        result1 = rf.encrypt_payload(data, self.phone_pub_key_b64)
        result2 = rf.encrypt_payload(data, self.phone_pub_key_b64)
        self.assertNotEqual(result1["ciphertext"], result2["ciphertext"])
        self.assertNotEqual(result1["iv"], result2["iv"])
        self.assertNotEqual(result1["ephemeralKey"], result2["ephemeralKey"])

    def test_encrypts_complex_data(self):
        data = {
            "toolName": "Bash",
            "params": {"command": "ls -la"},
            "output": "file1\nfile2\n",
            "success": True,
        }
        result = rf.encrypt_payload(data, self.phone_pub_key_b64)
        self.assertIn("ciphertext", result)
        self.assertGreater(len(result["ciphertext"]), 10)


class TestEnvelopeStructure(unittest.TestCase):
    """Verify the envelope built by process_hook conforms to the expected format.

    We mock the HTTP call and config loading to test structure only.
    """

    @patch.object(rf, "post_to_relay", return_value=True)
    @patch.object(rf, "load_config")
    @patch.object(rf, "get_phone_public_key", return_value=None)
    def test_envelope_has_required_fields(self, mock_key, mock_config, mock_post):
        mock_config.return_value = {"relay_url": "http://test:8765", "token": "test-token"}

        body = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "test-session-123",
            "prompt": "What is my IP?",
        }
        rf.process_hook(body)

        # Check the envelope that was posted
        envelope = mock_post.call_args[0][2]
        required = ["type", "id", "msgType", "correlationId", "sessionId", "from", "timestamp", "encrypted", "data"]
        for field in required:
            self.assertIn(field, envelope, f"Missing field: {field}")

    @patch.object(rf, "post_to_relay", return_value=True)
    @patch.object(rf, "load_config")
    @patch.object(rf, "get_phone_public_key", return_value=None)
    def test_envelope_event_type_mapping(self, mock_key, mock_config, mock_post):
        mock_config.return_value = {"relay_url": "http://test:8765", "token": "test-token"}

        test_cases = [
            ("SessionStart", "session.start"),
            ("UserPromptSubmit", "message.user"),
            ("PreToolUse", "tool.request"),
            ("PostToolUse", "tool.result"),
            ("PermissionRequest", "tool.permission_request"),
            ("Stop", "message.assistant"),
            ("SessionEnd", "session.end"),
            ("Notification", "notification"),
        ]

        for hook_name, expected_type in test_cases:
            mock_post.reset_mock()
            body = {"hook_event_name": hook_name, "session_id": "s1"}
            if hook_name == "Notification":
                body["notification_type"] = "idle_prompt"

            rf.process_hook(body)
            # Use first call (Stop hook sends 2 POSTs — main envelope + idle notification)
            envelope = mock_post.call_args_list[0][0][2]
            self.assertEqual(envelope["type"], expected_type, f"Mismatch for {hook_name}")

    @patch.object(rf, "post_to_relay", return_value=True)
    @patch.object(rf, "load_config")
    @patch.object(rf, "get_phone_public_key", return_value=None)
    def test_envelope_from_field(self, mock_key, mock_config, mock_post):
        mock_config.return_value = {"relay_url": "http://test:8765", "token": "test-token"}
        body = {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}
        rf.process_hook(body)
        envelope = mock_post.call_args[0][2]
        self.assertEqual(envelope["from"], "desktop")

    @patch.object(rf, "post_to_relay")
    @patch.object(rf, "load_config")
    @patch.object(rf, "get_phone_public_key")
    def test_encrypted_flag_when_key_present(self, mock_key, mock_config, mock_post):
        mock_config.return_value = {"relay_url": "http://test:8765", "token": "test-token"}
        mock_post.return_value = True
        mock_key.return_value = self._dummy_pubkey()

        body = {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}
        rf.process_hook(body)
        envelope = mock_post.call_args[0][2]
        self.assertTrue(envelope["encrypted"])
        self.assertIn("ephemeralKey", envelope["data"])

    @patch.object(rf, "post_to_relay", return_value=True)
    @patch.object(rf, "load_config")
    @patch.object(rf, "get_phone_public_key", return_value=None)
    def test_pre_tool_use_has_correlation_id(self, mock_key, mock_config, mock_post):
        mock_config.return_value = {"relay_url": "http://test:8765", "token": "test-token"}
        body = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"cmd": "ls"},
        }
        rf.process_hook(body)
        envelope = mock_post.call_args[0][2]
        self.assertIsNotNone(envelope["correlationId"])

    def _dummy_pubkey(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        key = ec.generate_private_key(ec.SECP256R1())
        return base64.b64encode(
            key.public_key().public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        ).decode()


class TestRetryLogic(unittest.TestCase):
    """Test HTTP retry configuration."""

    def test_default_retry_config(self):
        self.assertEqual(rf.DEFAULT_RETRY_CONFIG["maxRetries"], 3)
        self.assertEqual(rf.DEFAULT_RETRY_CONFIG["baseDelayMs"], 1000)
        self.assertEqual(rf.DEFAULT_RETRY_CONFIG["maxDelayMs"], 30000)

    def test_non_retryable_statuses(self):
        non_retryable = {400, 401, 403, 404, 405, 409, 410, 422}
        self.assertEqual(rf.NON_RETRYABLE_STATUS, non_retryable)

    def test_compute_delay_returns_float(self):
        delay = rf.compute_delay(0, 1000, 30000)
        self.assertIsInstance(delay, float)
        self.assertGreaterEqual(delay, 0)
        self.assertLessEqual(delay, 1.0)  # first attempt: capped at 1000ms / 1000 = 1.0s


if __name__ == "__main__":
    unittest.main()
