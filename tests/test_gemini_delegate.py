"""
Unit tests for Gemini model fallback runner.
Run with: python -m unittest discover tests
"""

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


class TestGeminiDelegate(unittest.TestCase):
    def test_capacity_detection(self):
        self.assertTrue(gemini_delegate.capacity_limited("status 429 Too Many Requests"))
        self.assertTrue(gemini_delegate.capacity_limited("No capacity available for model"))
        self.assertFalse(gemini_delegate.capacity_limited("SyntaxError in prompt"))

    def test_parse_duration_seconds(self):
        self.assertEqual(gemini_delegate.parse_duration_seconds("quota will reset after 1s"), 1)
        self.assertEqual(gemini_delegate.parse_duration_seconds("reset after 2 minutes"), 120)
        self.assertEqual(gemini_delegate.parse_duration_seconds("no reset hint"), 0)

    def test_falls_back_after_capacity_error(self):
        calls = []

        def fake_run(command, model, prompt, timeout):
            calls.append(model)
            if model == "flash":
                return FakeResult(1, stderr="No capacity available for model flash on the server")
            return FakeResult(0, stdout="ok from lite\n")

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--models", "flash,lite", "--no-state", "hello"]):
            with mock.patch.object(gemini_delegate, "resolve_gemini_command", return_value="gemini.cmd"):
                with mock.patch.object(gemini_delegate, "run_gemini", side_effect=fake_run):
                    with mock.patch.object(sys.stdout, "write") as write:
                        code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["flash", "lite"])
        write.assert_called_with("ok from lite\n")

    def test_research_profile_uses_pro_first(self):
        calls = []

        def fake_run(command, model, prompt, timeout):
            calls.append(model)
            return FakeResult(0, stdout="ok\n")

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--profile", "research", "--no-state", "hello"]):
            with mock.patch.object(gemini_delegate, "resolve_gemini_command", return_value="gemini.cmd"):
                with mock.patch.object(gemini_delegate, "run_gemini", side_effect=fake_run):
                    with mock.patch.object(sys.stdout, "write"):
                        code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["gemini-2.5-pro"])


if __name__ == "__main__":
    unittest.main()
