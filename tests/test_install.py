"""
Unit tests for installer CLI selection.
Run with: python -m unittest discover tests
"""

import importlib
import json
import os
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
    ensure_root_claude_bridge,
    interactive_selection,
)

enhanced_setup = importlib.import_module("setup")


def fake_discovered():
    return {
        "gemini": {
            "name": "Gemini CLI",
            "command": "gemini",
            "description": "Google's Gemini models via CLI",
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
    def test_defaults_to_gemini_only(self):
        selected = interactive_selection(fake_discovered())
        self.assertEqual(list(selected.keys()), ["gemini"])

    def test_enable_cli_adds_installed_extra(self):
        selected = interactive_selection(
            fake_discovered(),
            enabled_cli_names=["gemini", "copilot"],
        )
        self.assertEqual(list(selected.keys()), ["gemini", "copilot"])

    def test_enable_all_selects_installed_supported_clis(self):
        selected = interactive_selection(fake_discovered(), enable_all=True)
        self.assertEqual(list(selected.keys()), ["gemini", "copilot"])

    def test_missing_requested_cli_is_skipped(self):
        selected = interactive_selection(
            fake_discovered(),
            enabled_cli_names=["gemini", "aider"],
        )
        self.assertEqual(list(selected.keys()), ["gemini"])


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
            self.assertIn(".Codex/hooks", content)

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


if __name__ == "__main__":
    unittest.main()
