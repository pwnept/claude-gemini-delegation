"""
Unit tests for delegation hooks.
Run with: python -m unittest discover tests
"""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add hooks to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from pre_delegate import detect_task_type, estimate_compression, build_prompt
from post_delegate import count_lines, estimate_tokens, validate_response
from analyze_metrics import parse_csv_line
import delegation_guard


class TestPreDelegate(unittest.TestCase):
    """Test pre-delegation hook."""
    
    def test_detect_shell_task(self):
        self.assertEqual(detect_task_type("npm ls"), "shell")
        self.assertEqual(detect_task_type("git log --oneline"), "shell")
        self.assertEqual(detect_task_type("pip freeze"), "shell")
    
    def test_detect_search_task(self):
        self.assertEqual(detect_task_type("search for TODO in code"), "search")
        self.assertEqual(detect_task_type("grep -r 'password' src/"), "search")
    
    def test_detect_analyze_task(self):
        self.assertEqual(detect_task_type("analyze the codebase"), "analyze")
        self.assertEqual(detect_task_type("review security vulnerabilities"), "analyze")
    
    def test_estimate_compression(self):
        self.assertEqual(estimate_compression("npm ls"), 5)
        self.assertEqual(estimate_compression("grep something"), 8)
        self.assertEqual(estimate_compression("analyze code"), 10)
    
    def test_build_prompt(self):
        prompt = build_prompt("shell", "npm ls", "Build analysis", 5)
        self.assertIn("CONTEXT: Build analysis", prompt)
        self.assertIn("npm ls", prompt)
        self.assertIn("<5 lines", prompt)


class TestPostDelegate(unittest.TestCase):
    """Test post-delegation hook."""
    
    def test_count_lines(self):
        text = "line 1\nline 2\nline 3"
        self.assertEqual(count_lines(text), 3)
        
        text_with_empty = "line 1\n\nline 2\n"
        self.assertEqual(count_lines(text_with_empty), 2)
    
    def test_estimate_tokens(self):
        text = "a" * 400  # 400 characters
        self.assertEqual(estimate_tokens(text), 100)
    
    def test_validate_response_success(self):
        response = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        is_valid, warnings = validate_response(response, 10)
        self.assertTrue(is_valid)
        self.assertEqual(len(warnings), 0)
    
    def test_validate_response_too_long(self):
        response = "\n".join([f"Line {i}" for i in range(20)])
        is_valid, warnings = validate_response(response, 10)
        self.assertFalse(is_valid)
        self.assertTrue(any("too long" in w.lower() for w in warnings))
    
    def test_validate_response_too_brief(self):
        response = "Short"
        is_valid, warnings = validate_response(response, 10)
        self.assertFalse(is_valid)
        self.assertTrue(any("brief" in w.lower() for w in warnings))


class TestAnalyzeMetrics(unittest.TestCase):
    """Test metrics parsing."""

    def test_parse_csv_line_with_comma_in_task(self):
        parsed = parse_csv_line('2026-05-06 10:00:00,"task, with comma",3,20')
        self.assertEqual(parsed, ("2026-05-06 10:00:00", "task, with comma", 3, 20))


class TestDelegationGuard(unittest.TestCase):
    """Test Claude Code PreToolUse guard routing."""

    def test_source_checkout_guidance_uses_source_hooks(self):
        self.assertEqual(delegation_guard._HOOK_PREFIX, "hooks")
        self.assertEqual(delegation_guard._RUNNER_PATH, "hooks/gemini_delegate.py")

    def run_guard(self, payload):
        stdin = io.StringIO(json.dumps(payload))
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin):
            with mock.patch.object(sys, "stderr", stderr):
                code = delegation_guard.main()
        return code, stderr.getvalue()

    def test_blocks_verbose_bash_command(self):
        code, stderr = self.run_guard(
            {"tool_name": "Bash", "tool_input": {"command": "npm ls"}}
        )

        self.assertEqual(code, 2)
        self.assertIn("delegation pattern", stderr)

    def test_allows_safe_bash_command(self):
        code, stderr = self.run_guard(
            {"tool_name": "Bash", "tool_input": {"command": "git status --short"}}
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
