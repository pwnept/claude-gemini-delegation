"""
Unit tests for agy model fallback runner.
Run with: python3 -m unittest discover tests
"""

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

    def test_default_profile_uses_flash_high(self):
        calls = []

        def fake_run(command, model, prompt, timeout, **kwargs):
            calls.append(model)
            return FakeResult(0, stdout="ok\n")

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--no-state", "hello"]):
            with mock.patch.object(gemini_delegate, "resolve_agy_command", return_value="agy.exe"):
                with mock.patch.object(gemini_delegate, "run_agy", side_effect=fake_run):
                    with mock.patch.object(sys.stdout, "write"):
                        code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["Gemini 3.5 Flash (High)"])

    def test_research_profile_uses_pro_high(self):
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
        self.assertEqual(calls, ["Gemini 3.1 Pro (High)"])


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

    def test_gemini_cli_backend_selected_by_env(self):
        args = mock.Mock(backend=None)
        with mock.patch.dict(os.environ, {"DELEGATION_BACKEND": "gemini-cli"}, clear=True):
            self.assertEqual(gemini_delegate.resolve_backend(args), "gemini-cli")


class TestGeminiCliBackend(unittest.TestCase):
    def test_gemini_cli_backend_falls_back_after_capacity_error(self):
        calls = []

        def fake_run(model, prompt, timeout):
            calls.append(model)
            if model == "gemini-3-flash-preview":
                return gemini_delegate.subprocess.CompletedProcess(
                    args=["gemini", "--model", model, "-p", "..."],
                    returncode=1,
                    stdout="",
                    stderr="RESOURCE_EXHAUSTED: quota exceeded",
                )
            return gemini_delegate.subprocess.CompletedProcess(
                args=["gemini", "--model", model, "-p", "..."],
                returncode=0,
                stdout="ok from flash\n",
                stderr="",
            )

        argv = [
            "gemini_delegate.py", "--backend", "gemini-cli",
            "--models", "gemini-3-flash-preview,gemini-2.5-flash", "--no-state", "hello",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(gemini_delegate, "run_gemini_cli", side_effect=fake_run):
                with mock.patch.object(sys.stdout, "write") as write:
                    code = gemini_delegate.main()

        self.assertEqual(code, 0)
        # uses explicit --models, so tests fallback mechanism not default list
        self.assertEqual(calls, ["gemini-3-flash-preview", "gemini-2.5-flash"])
        write.assert_called_with("ok from flash\n")

    def test_gemini_cli_falls_back_on_exit0_empty_stdout(self):
        """gemini-cli exits 0 with empty stdout after 503 retries; must trigger fallback."""
        calls = []

        def fake_run(model, prompt, timeout):
            calls.append(model)
            if model == "model-a":
                # real behavior: internal retries exhausted, exit 0, no output
                return gemini_delegate.subprocess.CompletedProcess(
                    args=["gemini", "--model", model],
                    returncode=0,
                    stdout="",
                    stderr="_ApiError: service unavailable (503)",
                )
            return gemini_delegate.subprocess.CompletedProcess(
                args=["gemini", "--model", model],
                returncode=0,
                stdout="4\n",
                stderr="",
            )

        argv = [
            "gemini_delegate.py", "--backend", "gemini-cli",
            "--models", "model-a,model-b", "--no-state", "hello",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(gemini_delegate, "run_gemini_cli", side_effect=fake_run):
                with mock.patch.object(sys.stdout, "write") as write:
                    code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["model-a", "model-b"])
        write.assert_called_with("4\n")

    def test_gemini_cli_default_models(self):
        calls = []

        def fake_run(model, prompt, timeout):
            calls.append(model)
            return gemini_delegate.subprocess.CompletedProcess(
                args=["gemini", "--model", model], returncode=0, stdout="ok\n", stderr="",
            )

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--backend", "gemini-cli", "--no-state", "hello"]):
            with mock.patch.object(gemini_delegate, "run_gemini_cli", side_effect=fake_run):
                with mock.patch.object(sys.stdout, "write"):
                    gemini_delegate.main()

        self.assertEqual(calls, ["gemini-3.5-flash"])


class TestGeminiApiBackend(unittest.TestCase):
    def test_gemini_api_backend_falls_back_on_429(self):
        calls = []

        def fake_call(model, prompt, timeout):
            calls.append(model)
            if model == "gemini-3-flash":
                return gemini_delegate.subprocess.CompletedProcess(
                    args=["gemini-api", model],
                    returncode=1,
                    stdout="",
                    stderr="429 RESOURCE_EXHAUSTED: quota exceeded",
                )
            return gemini_delegate.subprocess.CompletedProcess(
                args=["gemini-api", model],
                returncode=0,
                stdout="4\n",
                stderr="",
            )

        argv = [
            "gemini_delegate.py", "--backend", "gemini-api",
            "--models", "gemini-3-flash,gemini-3.5-flash", "--no-state", "hello",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(gemini_delegate, "call_gemini_api", side_effect=fake_call):
                with mock.patch.object(sys.stdout, "write") as write:
                    code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["gemini-3-flash", "gemini-3.5-flash"])
        write.assert_called_with("4\n")

    def test_gemini_api_default_models_cascade(self):
        models = gemini_delegate.DEFAULT_API_MODELS
        self.assertEqual(models[0], "gemini-3.5-flash")
        self.assertEqual(models[1], "gemini-3-flash")
        self.assertGreaterEqual(len(models), 5)
        # lite models must come last
        lite = [m for m in models if m in gemini_delegate.LITE_MODELS]
        non_lite = [m for m in models if m not in gemini_delegate.LITE_MODELS]
        self.assertTrue(all(models.index(l) > models.index(n) for l in lite for n in non_lite))

    def test_gemini_api_no_key_returns_nonzero(self):
        argv = ["gemini_delegate.py", "--backend", "gemini-api", "--no-state", "hello"]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict(os.environ, {}, clear=True):
                code = gemini_delegate.main()
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
