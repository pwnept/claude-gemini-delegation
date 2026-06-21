import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gemini_delegation.installer import (  # noqa: E402
    AGENTS_MARKER_BEGIN,
    AGENTS_MARKER_END,
    InstallError,
    create_claude_settings,
    get_codex_dir,
    install_hooks,
    remove_agents_md_section,
    uninstall_hooks,
    verify_install,
)


class TestTargetInstall(unittest.TestCase):
    def test_install_migrates_claude_to_agents_and_creates_local_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "CLAUDE.md").write_text("# Local Claude rule\nKeep me.\n", encoding="utf-8")

            result = install_hooks(target_dir=tmpdir)

            self.assertEqual(result, 0)
            self.assertEqual((project_dir / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")
            agents_text = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("# Local Claude rule\nKeep me.", agents_text)
            self.assertIn(AGENTS_MARKER_BEGIN, agents_text)
            self.assertIn(AGENTS_MARKER_END, agents_text)
            self.assertTrue((project_dir / ".gemini-delegation" / "hooks" / "gemini_delegate.py").is_file())
            self.assertTrue((project_dir / ".claude" / "hooks" / "delegate_and_log.ps1").is_file())
            self.assertTrue((project_dir / ".codex" / "hooks" / "delegate_and_log.ps1").is_file())
            self.assertTrue((project_dir / ".agents" / "rules" / "delegation.md").is_file())
            self.assertTrue((project_dir / "agents" / "code-review-agent-dave" / "dave_audit.md").is_file())

    def test_install_skips_claude_migration_when_agents_already_has_same_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            shared = "# Shared instructions\nKeep this once.\n"
            (project_dir / "AGENTS.md").write_text(shared, encoding="utf-8")
            (project_dir / "CLAUDE.md").write_text(shared, encoding="utf-8")

            install_hooks(target_dir=tmpdir)

            agents_text = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(agents_text.count("# Shared instructions"), 1)
            self.assertNotIn("## Migrated CLAUDE.md Instructions", agents_text)

    def test_verify_rejects_incomplete_install(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
            (project_dir / "AGENTS.md").write_text(
                AGENTS_MARKER_BEGIN + "\nmanaged\n" + AGENTS_MARKER_END + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(InstallError):
                verify_install(tmpdir)

    def test_uninstall_removes_managed_files_and_writes_latest_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            install_hooks(target_dir=tmpdir)

            result = uninstall_hooks(target_dir=tmpdir)

            self.assertEqual(result, 0)
            self.assertFalse((project_dir / ".gemini-delegation").exists())
            self.assertFalse((project_dir / ".claude" / "hooks" / "delegate_and_log.ps1").exists())
            agents_text = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertNotIn(AGENTS_MARKER_BEGIN, agents_text)
            report = project_dir / "temp" / "delegation-uninstall-latest.md"
            self.assertTrue(report.is_file())
            self.assertIn("Delegation Uninstall Report", report.read_text(encoding="utf-8"))


class TestManagedDocuments(unittest.TestCase):
    def test_mismatched_agents_markers_stop_install(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "AGENTS.md").write_text(AGENTS_MARKER_BEGIN + "\nmissing end\n", encoding="utf-8")

            with self.assertRaises(InstallError):
                install_hooks(target_dir=tmpdir)

    def test_remove_agents_section_preserves_user_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "AGENTS.md").write_text(
                "# Rules\nKeep.\n\n"
                + AGENTS_MARKER_BEGIN
                + "\nmanaged\n"
                + AGENTS_MARKER_END
                + "\n\nTail.\n",
                encoding="utf-8",
            )

            changed = remove_agents_md_section(project_dir)

            self.assertTrue(changed)
            content = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("# Rules\nKeep.", content)
            self.assertIn("Tail.", content)
            self.assertNotIn("managed", content)

    def test_create_claude_settings_replaces_old_delegation_guard(self):
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
                                    "hooks": [{"type": "command", "command": "python .claude/hooks/delegation_guard.py"}],
                                },
                                {
                                    "matcher": "Bash",
                                    "hooks": [{"type": "command", "command": "echo keep"}],
                                },
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            create_claude_settings(claude_dir)

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            commands = [
                hook["command"]
                for entry in settings["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
            ]
            self.assertIn("echo keep", commands)
            self.assertEqual(sum("delegation_guard.ps1" in command for command in commands), 2)
            self.assertFalse(any("delegation_guard.py" in command for command in commands))

    def test_migrates_legacy_codex_casing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / ".Codex").mkdir()

            codex_dir = get_codex_dir(project_dir)
            child_names = {child.name for child in project_dir.iterdir()}

            self.assertEqual(codex_dir.name, ".codex")
            self.assertTrue(codex_dir.is_dir())
            self.assertIn(".codex", child_names)
            self.assertNotIn(".Codex", child_names)


if __name__ == "__main__":
    unittest.main()
