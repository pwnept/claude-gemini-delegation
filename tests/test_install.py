"""
Unit tests for installer CLI selection.
Run with: python -m unittest discover tests
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from install import (
    AGENTS_MARKER_BEGIN,
    AGENTS_MARKER_END,
    build_root_claude_bridge,
    extract_migrated_claude_content,
    ensure_agents_md,
    ensure_dot_claude_bridge,
    ensure_root_claude_bridge,
    interactive_selection,
)

enhanced_setup = importlib.import_module("setup")


def fake_discovered():
    return {
        "agy": {
            "name": "agy (Antigravity CLI)",
            "command": "agy",
            "description": "Google Antigravity models via agy CLI",
            "installed": True,
        },
        "aider": {
            "name": "Aider",
            "command": "aider",
            "description": "AI pair programming in the terminal",
            "installed": False,
        },
        "copilot": {
            "name": "GitHub Copilot CLI",
            "command": "gh copilot",
            "description": "GitHub Copilot extensions for gh",
            "installed": True,
        },
    }


class TestInstallerSelection(unittest.TestCase):
    def test_defaults_to_agy_only(self):
        selected = interactive_selection(fake_discovered())
        self.assertEqual(list(selected.keys()), ["agy"])

    def test_enable_cli_adds_installed_extra(self):
        selected = interactive_selection(
            fake_discovered(),
            enabled_cli_names=["agy", "copilot"],
        )
        self.assertEqual(list(selected.keys()), ["agy", "copilot"])

    def test_enable_all_selects_installed_supported_clis(self):
        selected = interactive_selection(fake_discovered(), enable_all=True)
        self.assertEqual(list(selected.keys()), ["agy", "copilot"])

    def test_missing_requested_cli_is_skipped(self):
        selected = interactive_selection(
            fake_discovered(),
            enabled_cli_names=["agy", "aider"],
        )
        self.assertEqual(list(selected.keys()), ["agy"])


class TestRootClaudeBridge(unittest.TestCase):
    def test_builds_bridge_before_existing_content(self):
        existing = "# Local instructions\nKeep these details.\n"

        content = build_root_claude_bridge(existing)

        self.assertEqual(content, "@AGENTS.md\n")

    def test_extracts_migrated_claude_content(self):
        existing = "@AGENTS.md\n@.claude/CLAUDE.md\n\n# Local instructions\nKeep these details.\n"

        content = extract_migrated_claude_content(existing)

        self.assertEqual(content, "# Local instructions\nKeep these details.\n")

    def test_creates_root_claude_bridge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            result = ensure_root_claude_bridge(project_dir)

            self.assertEqual(result, project_dir / "CLAUDE.md")
            self.assertEqual((project_dir / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")

    def test_existing_root_claude_bridge_needs_no_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            claude_md = project_dir / "CLAUDE.md"
            claude_md.write_text("@AGENTS.md\n", encoding="utf-8")

            ensure_root_claude_bridge(project_dir)

            self.assertEqual(claude_md.read_text(encoding="utf-8"), "@AGENTS.md\n")
            self.assertEqual(len(list(project_dir.glob("CLAUDE.md.bak.*"))), 0)

    def test_root_claude_bridge_does_not_preserve_existing_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            claude_md = project_dir / "CLAUDE.md"
            claude_md.write_text("# Project rules\nDo not lose this.\n", encoding="utf-8")

            ensure_root_claude_bridge(project_dir)

            self.assertEqual(claude_md.read_text(encoding="utf-8"), "@AGENTS.md\n")
            self.assertEqual(len(list(project_dir.glob("CLAUDE.md.bak.*"))), 1)


class TestDotClaudeMigration(unittest.TestCase):
    def test_removes_redundant_dot_claude_bridge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            bridge = claude_dir / "CLAUDE.md"
            bridge.write_text("@../AGENTS.md\n", encoding="utf-8")

            migrated = ensure_dot_claude_bridge(claude_dir)

            self.assertEqual(migrated, "")
            self.assertFalse(bridge.exists())

    def test_migrates_dot_claude_user_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            claude_md = claude_dir / "CLAUDE.md"
            claude_md.write_text("# Local Claude rule\nKeep this.\n", encoding="utf-8")

            migrated = ensure_dot_claude_bridge(claude_dir)

            self.assertEqual(migrated, "# Local Claude rule\nKeep this.\n")
            self.assertFalse(claude_md.exists())
            self.assertEqual(len(list(claude_dir.glob("CLAUDE.md.bak.*"))), 1)


class TestAgentsMd(unittest.TestCase):
    def test_creates_agents_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            result = ensure_agents_md(project_dir)

            content = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(result, project_dir / "AGENTS.md")
            self.assertIn("# Agent Instructions", content)
            self.assertIn(AGENTS_MARKER_BEGIN, content)
            self.assertIn(AGENTS_MARKER_END, content)
            self.assertIn(".claude/hooks", content)
            self.assertIn(".codex/hooks", content)

    def test_preserves_existing_agents_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            agents_md = project_dir / "AGENTS.md"
            agents_md.write_text("# Project Agents\nKeep this.\n", encoding="utf-8")

            ensure_agents_md(project_dir)

            content = agents_md.read_text(encoding="utf-8")
            self.assertIn("# Project Agents\nKeep this.", content)
            self.assertIn(AGENTS_MARKER_BEGIN, content)
            self.assertEqual(len(list(project_dir.glob("AGENTS.md.bak.*"))), 1)

    def test_updates_existing_managed_agents_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            agents_md = project_dir / "AGENTS.md"
            agents_md.write_text(
                "# Project Agents\n\n"
                + AGENTS_MARKER_BEGIN
                + "\nold\n"
                + AGENTS_MARKER_END
                + "\n\nTail.\n",
                encoding="utf-8",
            )

            ensure_agents_md(project_dir)

            content = agents_md.read_text(encoding="utf-8")
            self.assertIn("# Project Agents", content)
            self.assertIn("Tail.", content)
            self.assertIn(".claude/hooks", content)
            self.assertNotIn("\nold\n", content)
            self.assertEqual(len(list(project_dir.glob("AGENTS.md.bak.*"))), 1)

    def test_migrates_existing_claude_content_into_agents_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            ensure_agents_md(project_dir, "# Project rules\nDo not lose this.\n")

            content = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("# Project rules\nDo not lose this.", content)
            self.assertIn(AGENTS_MARKER_BEGIN, content)

    def test_removes_obsolete_default_agents_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            agents_md = project_dir / "AGENTS.md"
            agents_md.write_text(
                "# Agent Instructions\n\n"
                "Gemini delegation is installed locally in `.claude/hooks`.\n\n"
                "The root `CLAUDE.md` also loads `.claude/CLAUDE.md`; follow that generated\n"
                "configuration for delegation presets, wrapper usage, and Gemini fallback\n"
                "behavior.\n",
                encoding="utf-8",
            )

            ensure_agents_md(project_dir)

            content = agents_md.read_text(encoding="utf-8")
            self.assertNotIn("also loads `.claude/CLAUDE.md`", content)
            self.assertIn(AGENTS_MARKER_BEGIN, content)


class TestClaudeSettings(unittest.TestCase):
    def expected_guard_fragment(self):
        return "delegation_guard.ps1" if os.name == "nt" else "delegation_guard.py"

    def guard_hooks(self, settings):
        pre_tool_use = settings.get("hooks", {}).get("PreToolUse", [])
        hooks = []
        for entry in pre_tool_use:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if "delegation_guard.py" in command or "delegation_guard.ps1" in command:
                    hooks.append((entry, hook))
        return hooks

    def test_create_claude_settings_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()

            enhanced_setup.create_claude_settings(claude_dir)
            enhanced_setup.create_claude_settings(claude_dir)

            settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            hooks = self.guard_hooks(settings)
            self.assertEqual(len(hooks), 2)
            matchers = {h[0]["matcher"] for h in hooks}
            self.assertEqual(matchers, {"Bash", "PowerShell"})
            self.assertIn(self.expected_guard_fragment(), hooks[0][1]["command"])
            self.assertEqual(len(list(claude_dir.glob("settings.json.bak.*"))), 0)

    def test_create_claude_settings_migrates_old_python_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            settings_path = claude_dir / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python .claude/hooks/delegation_guard.py",
                                            "timeout": 5,
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            enhanced_setup.create_claude_settings(claude_dir)

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            hooks = self.guard_hooks(settings)
            self.assertEqual(len(hooks), 2)
            matchers = {h[0]["matcher"] for h in hooks}
            self.assertEqual(matchers, {"Bash", "PowerShell"})
            self.assertIn(self.expected_guard_fragment(), hooks[0][1]["command"])
            self.assertEqual(len(list(claude_dir.glob("settings.json.bak.*"))), 1)


class TestCrossToolInstall(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell test")
    def test_delegate_and_log_forwards_task_and_prompt_stdin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / "hooks"
            shutil.copytree(Path(__file__).parent.parent / "hooks", hooks_dir)
            (hooks_dir / "gemini_delegate.py").write_text(
                "import sys\nprint(sys.stdin.read())\n",
                encoding="utf-8",
            )
            (hooks_dir / "post_delegate.py").write_text(
                "import sys\nsys.exit(0)\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(hooks_dir / "delegate_and_log.ps1"),
                    "task-marker",
                    "context-marker",
                    "5",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("task-marker", result.stdout)
            self.assertIn("context-marker", result.stdout)

    def test_migrates_legacy_codex_directory_casing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / ".Codex").mkdir()

            codex_dir = enhanced_setup.get_codex_dir(project_dir)
            child_names = {child.name for child in project_dir.iterdir()}

            self.assertEqual(codex_dir.name, ".codex")
            self.assertIn(".codex", child_names)
            self.assertNotIn(".Codex", child_names)

    def test_antigravity_rule_prevents_recursive_agy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rule_path = enhanced_setup.create_antigravity_rule(Path(tmpdir))
            content = rule_path.read_text(encoding="utf-8")

            self.assertIn("AGENTS.md", content)
            self.assertIn("Do not recursively invoke `agy`", content)

    def test_target_install_creates_all_tool_entry_points(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent.parent / "setup.py"),
                    "--target",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "DELEGATION_SKIP_REGISTRY": "1"},
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            project_dir = Path(tmpdir)
            self.assertTrue((project_dir / "AGENTS.md").is_file())
            self.assertEqual(
                (project_dir / "CLAUDE.md").read_text(encoding="utf-8"),
                "@AGENTS.md\n",
            )
            self.assertTrue((project_dir / ".claude" / "settings.json").is_file())
            self.assertTrue((project_dir / ".claude" / "hooks" / "delegate.ps1").is_file())
            self.assertFalse((project_dir / ".claude" / "CLAUDE.md").exists())
            self.assertTrue((project_dir / ".codex" / "hooks" / "delegate.ps1").is_file())
            self.assertFalse((project_dir / ".codex" / "settings.json").exists())
            self.assertTrue((project_dir / ".agents" / "rules" / "delegation.md").is_file())
            installed_agent_dir = project_dir / "agents" / "code-review-agent-dave"
            self.assertTrue((installed_agent_dir / "dave_audit.md").is_file())
            audit_script = (installed_agent_dir / "generate-audit.ps1").read_text(encoding="utf-8")
            self.assertIn(".claude\\hooks\\delegate_and_log.ps1", audit_script)
            self.assertNotIn("git commit", audit_script)


if __name__ == "__main__":
    unittest.main()
