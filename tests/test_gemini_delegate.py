"""
Unit tests for agy model fallback runner.
Run with: python3 -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import gemini_delegate
import delegation_caller


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

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--models", "flash,lite", "--no-state", "--no-save", "hello"]):
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

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--no-state", "--no-save", "hello"]):
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

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--profile", "research", "--no-state", "--no-save", "hello"]):
            with mock.patch.object(gemini_delegate, "resolve_agy_command", return_value="agy.exe"):
                with mock.patch.object(gemini_delegate, "run_agy", side_effect=fake_run):
                    with mock.patch.object(sys.stdout, "write"):
                        code = gemini_delegate.main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["Gemini 3.1 Pro (High)"])


class TestDelegationTranscriptLogs(unittest.TestCase):
    def test_save_delegation_transcript_writes_one_user_home_txt_per_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agent_dir = root / "repo" / ".gemini-delegation"
            agent_dir.mkdir(parents=True)
            (agent_dir / ".caller-session.json").write_text(
                json_text(
                    {
                        "agent": "codex",
                        "session_id": "2026/06/29/session.jsonl",
                        "transcript_path": str(Path("C:/Users/User/.codex/sessions/2026/06/29/session.jsonl")),
                        "turn_id": "turn-7",
                    }
                ),
                encoding="utf-8",
            )
            args = mock.Mock(caller="auto", profile="research")

            with mock.patch.dict(os.environ, {"DELEGATION_LOG_ROOT": str(root / "home")}, clear=True):
                first = gemini_delegate._save_delegation_transcript(
                    "prompt text", "output text", "model-a", agent_dir, args, "agy"
                )
                second = gemini_delegate._save_delegation_transcript(
                    "prompt text", "output text 2", "model-a", agent_dir, args, "agy"
                )

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(first.suffix, ".txt")
            self.assertEqual(second.name, "turn-7_0002.txt")
            self.assertIn("runs", first.parts)
            self.assertIn("codex", first.parts)
            self.assertIn("repo", first.parts[-3])
            self.assertEqual(first.parent.name, "session_gemini_delegation")
            content = first.read_text(encoding="utf-8")
            self.assertIn("backend: agy", content)
            self.assertIn("profile: research", content)
            self.assertIn("model: model-a", content)
            self.assertIn("=== PROMPT ===\nprompt text", content)
            self.assertIn("=== OUTPUT ===\noutput text", content)

    def test_save_delegation_transcript_respects_disable_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agent_dir = root / "repo" / ".gemini-delegation"
            agent_dir.mkdir(parents=True)
            args = mock.Mock(caller="codex", profile="default")

            with mock.patch.dict(
                os.environ,
                {"DELEGATION_LOG_ROOT": str(root / "home"), "DELEGATION_DISABLE_LOGS": "1"},
                clear=True,
            ):
                result = gemini_delegate._save_delegation_transcript(
                    "prompt", "output", "model", agent_dir, args, "agy"
                )

            self.assertIsNone(result)
            self.assertFalse((root / "home").exists())


def json_text(value: dict) -> str:
    import json

    return json.dumps(value)


class TestDetectCaller(unittest.TestCase):
    """Tests for delegation_caller.detect_caller() — the layered harness detection."""

    def _detect(self, env: dict) -> str:
        with mock.patch.dict(os.environ, env, clear=True):
            return delegation_caller.detect_caller()

    def test_delegation_caller_token_wins_over_sniff(self):
        # Explicit token takes priority even when Claude env vars are present
        env = {"DELEGATION_CALLER": "codex", "CLAUDECODE": "1"}
        self.assertEqual(self._detect(env), "codex")

    def test_delegation_caller_token_invalid_falls_through_to_sniff(self):
        # Invalid token → ignore token, fall through to sniff
        env = {"DELEGATION_CALLER": "unknown_tool", "CLAUDECODE": "1"}
        self.assertEqual(self._detect(env), "claude")

    def test_claude_sniff_claudecode_var(self):
        self.assertEqual(self._detect({"CLAUDECODE": "1"}), "claude")

    def test_claude_sniff_entrypoint_var(self):
        self.assertEqual(self._detect({"CLAUDE_CODE_ENTRYPOINT": "cli"}), "claude")

    def test_claude_sniff_ai_agent_prefix(self):
        self.assertEqual(self._detect({"AI_AGENT": "claude-code_2-1-195_agent"}), "claude")

    def test_codex_sniff_codex_env_prefix(self):
        self.assertEqual(self._detect({"CODEX_TASK_ID": "abc123"}), "codex")

    def test_codex_sniff_ai_agent_prefix(self):
        self.assertEqual(self._detect({"AI_AGENT": "codex-something"}), "codex")

    def test_agy_sniff_antigravity_prefix(self):
        self.assertEqual(self._detect({"ANTIGRAVITY_SESSION": "1"}), "agy")

    def test_agy_sniff_agy_prefix(self):
        self.assertEqual(self._detect({"AGY_MODEL": "gemini-flash"}), "agy")

    def test_unknown_env_returns_empty_string(self):
        self.assertEqual(self._detect({}), "")

    def test_resolve_log_dir_auto_with_token_routes_correctly(self):
        home = Path.home()
        with mock.patch.dict(os.environ, {"DELEGATION_CALLER": "agy"}, clear=True):
            result = delegation_caller.resolve_log_dir("auto", Path("/fallback"))
        self.assertEqual(result, home / ".gemini" / "delegation-logs")

    def test_resolve_log_dir_unknown_caller_writes_readme(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            fallback = Path(tmpdir) / "metrics"
            with mock.patch.dict(os.environ, {}, clear=True):
                result = delegation_caller.resolve_log_dir("auto", fallback)
            self.assertEqual(result, fallback)
            readme = fallback / "README.txt"
            self.assertTrue(readme.exists(), "README.txt should be written on fallback")
            content = readme.read_text(encoding="utf-8")
            self.assertIn("DELEGATION_CALLER", content)


class TestResolveLogDir(unittest.TestCase):
    """Routing table sourced from delegation_caller.CALLER_LOG_DIRS."""

    def test_known_callers_map_to_harness_home(self):
        home = Path.home()
        self.assertEqual(
            delegation_caller.resolve_log_dir("claude", Path("/fallback")),
            home / ".claude" / "delegation-logs",
        )
        self.assertEqual(
            delegation_caller.resolve_log_dir("codex", Path("/fallback")),
            home / ".codex" / "delegation-logs",
        )
        self.assertEqual(
            delegation_caller.resolve_log_dir("agy", Path("/fallback")),
            home / ".gemini" / "delegation-logs",
        )

    def test_unknown_caller_uses_fallback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            fallback = Path(tmpdir) / "metrics"
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(delegation_caller.resolve_log_dir("unknown", fallback), fallback)


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
            "--models", "gemini-3-flash-preview,gemini-2.5-flash", "--no-state", "--no-save", "hello",
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
            "--models", "model-a,model-b", "--no-state", "--no-save", "hello",
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

        with mock.patch.object(sys, "argv", ["gemini_delegate.py", "--backend", "gemini-cli", "--no-state", "--no-save", "hello"]):
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
            "--models", "gemini-3-flash,gemini-3.5-flash", "--no-state", "--no-save", "hello",
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
