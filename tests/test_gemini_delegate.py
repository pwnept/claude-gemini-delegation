"""
Unit tests for agy model fallback runner.
Run with: python3 -m unittest discover tests
"""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import gemini_delegate


class FakeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestAgyDelegate(unittest.TestCase):
    def test_capacity_detection(self):
        self.assertTrue(gemini_delegate.capacity_limited("status 429 Too Many Requests"))
        self.assertTrue(gemini_delegate.capacity_limited("No capacity available for model"))
        self.assertFalse(gemini_delegate.capacity_limited("SyntaxError in prompt"))

    def test_parse_duration_seconds(self):
        self.assertEqual(gemini_delegate.parse_duration_seconds("quota will reset after 1s"), 1)
        self.assertEqual(gemini_delegate.parse_duration_seconds("reset after 2 minutes"), 120)
        self.assertEqual(gemini_delegate.parse_duration_seconds("no reset hint"), 0)

    def test_parse_model_order_preserves_qualified_model_names(self):
        self.assertEqual(
            gemini_delegate.parse_model_order("Gemini 3.5 Flash (Medium)"),
            ["Gemini 3.5 Flash (Medium)"],
        )
        self.assertEqual(
            gemini_delegate.parse_model_order('"Gemini 3.1 Pro (Low)" "Gemini 3.5 Flash (Medium)"'),
            ["Gemini 3.1 Pro (Low)", "Gemini 3.5 Flash (Medium)"],
        )
        self.assertEqual(
            gemini_delegate.parse_model_order('"Gemini 3.1 Pro (Low)","Gemini 3.5 Flash (Medium)"'),
            ["Gemini 3.1 Pro (Low)", "Gemini 3.5 Flash (Medium)"],
        )

    def test_incomplete_model_names_are_rejected(self):
        errors = gemini_delegate.model_name_errors(["Gemini 3.5 Flash"])

        self.assertEqual(len(errors), 1)
        self.assertIn("Gemini 3.5 Flash (Medium)", errors[0])
        self.assertIn("Gemini 3.5 Flash (High)", errors[0])
        self.assertIn("Gemini 3.5 Flash (Low)", errors[0])

    def test_falls_back_after_capacity_error(self):
        calls = []

        def fake_run(command, model, prompt, timeout, **kwargs):
            calls.append(model)
            if model == "flash":
                return FakeResult(1, stderr="No capacity available for model flash on the server")
            return FakeResult(0, stdout="ok from lite\n")

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--models", "flash,lite", "--no-state", "hello"]):
            with mock.patch.object(gemini_delegate, "resolve_agy_command", return_value="agy.exe"):
                with mock.patch.object(gemini_delegate, "run_agy", side_effect=fake_run):
                    with mock.patch.object(sys.stdout, "write") as write:
                        code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["flash", "lite"])
        write.assert_called_with("ok from lite\n")

    def test_research_profile_uses_pro_first(self):
        calls = []

        def fake_run(command, model, prompt, timeout, **kwargs):
            calls.append(model)
            return FakeResult(0, stdout="ok\n")

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--profile", "research", "--no-state", "hello"]):
            with mock.patch.object(gemini_delegate, "resolve_agy_command", return_value="agy.exe"):
                with mock.patch.object(gemini_delegate, "run_agy", side_effect=fake_run):
                    with mock.patch.object(sys.stdout, "write"):
                        code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["Gemini 3.1 Pro (Low)"])


class TestBackendSelection(unittest.TestCase):
    def test_resolve_backend_precedence(self):
        args = mock.Mock(backend=None)

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gemini_delegate.resolve_backend(args), "agy")

        with mock.patch.dict(os.environ, {"DELEGATION_BACKEND": "gemini-api"}, clear=True):
            self.assertEqual(gemini_delegate.resolve_backend(args), "gemini-api")

        args.backend = "agy"
        with mock.patch.dict(os.environ, {"DELEGATION_BACKEND": "gemini-api"}, clear=True):
            self.assertEqual(gemini_delegate.resolve_backend(args), "agy")

    def test_gemini_api_backend_requires_api_key(self):
        argv = ["gemini_delegate.py", "--backend", "gemini-api", "--no-state", "hello"]
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(sys.stderr, "write") as write:
                    code = gemini_delegate.main()

        self.assertEqual(code, 2)
        written = "".join(call.args[0] for call in write.call_args_list)
        self.assertIn("GEMINI_API_KEY", written)


class TestGeminiApiBackend(unittest.TestCase):
    def test_call_gemini_api_success(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "hello world"}]}}]}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
                result = gemini_delegate.call_gemini_api("gemini-2.5-flash", "hi", timeout=10)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "hello world")

    def test_call_gemini_api_capacity_error_is_classified(self):
        import urllib.error

        error_body = json.dumps(
            {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota exceeded"}}
        ).encode("utf-8")

        def raise_http_error(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://example.invalid",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=io.BytesIO(error_body),
            )

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=raise_http_error):
                result = gemini_delegate.call_gemini_api("gemini-2.5-flash", "hi", timeout=10)

        self.assertEqual(result.returncode, 429)
        self.assertTrue(gemini_delegate.capacity_limited(result.stderr))

    def test_gemini_api_backend_falls_back_after_capacity_error(self):
        calls = []

        def fake_call(model, prompt, timeout):
            calls.append(model)
            if model == "gemini-2.5-pro":
                return gemini_delegate.subprocess.CompletedProcess(
                    args=["gemini-api", model],
                    returncode=429,
                    stdout="",
                    stderr='{"error": {"status": "RESOURCE_EXHAUSTED"}}',
                )
            return gemini_delegate.subprocess.CompletedProcess(
                args=["gemini-api", model], returncode=0, stdout="ok from flash\n", stderr="",
            )

        argv = [
            "gemini_delegate.py", "--backend", "gemini-api",
            "--models", "gemini-2.5-pro,gemini-2.5-flash", "--no-state", "hello",
        ]
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(gemini_delegate, "call_gemini_api", side_effect=fake_call):
                    with mock.patch.object(sys.stdout, "write") as write:
                        code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["gemini-2.5-pro", "gemini-2.5-flash"])
        write.assert_called_with("ok from flash\n")


if __name__ == "__main__":
    unittest.main()
