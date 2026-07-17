import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gemini_delegation.installer import (  # noqa: E402
    AGENTS_MARKER_BEGIN,
    AGENTS_MARKER_END,
    InstallError,
    agents_section,
    check_for_update,
    create_claude_command,
    create_claude_settings,
    get_codex_dir,
    install_hooks,
    remove_agents_md_section,
    revert_claude_settings,
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
            self.assertTrue((project_dir / ".gemini-delegation" / "hooks" / "delegate_and_log.ps1").is_file())
            self.assertTrue((project_dir / ".claude" / "commands" / "delegate.md").is_file())
            self.assertTrue((project_dir / ".agents" / "rules" / "delegation.md").is_file())
            # shims must NOT be created in the no-shim layout
            self.assertFalse((project_dir / ".claude" / "hooks" / "delegate_and_log.ps1").exists())
            self.assertFalse((project_dir / ".codex" / "hooks" / "delegate_and_log.ps1").exists())
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


class TestAgentsSection(unittest.TestCase):
    def test_slim_agents_section_is_concise(self):
        """The AGENTS.md managed block must be short - it's always-on context."""
        content = agents_section()
        inner_lines = [
            line for line in content.splitlines()
            if line.strip() not in {AGENTS_MARKER_BEGIN, AGENTS_MARKER_END}
        ]
        self.assertLess(len(inner_lines), 18, "agents_section grew too large; keep it slim")

    def test_agents_section_contains_delegate_command(self):
        content = agents_section()
        self.assertIn("delegate_and_log.ps1", content)
        self.assertIn("agy", content.lower())
        self.assertIn(AGENTS_MARKER_BEGIN, content)
        self.assertIn(AGENTS_MARKER_END, content)

    def test_agents_section_has_no_caller_flag(self):
        """Call instruction must be identical for every harness - no -Caller in examples."""
        content = agents_section()
        self.assertNotIn("-Caller", content)

    def test_agents_section_mentions_fallback_backends(self):
        content = agents_section()
        self.assertIn("DELEGATION_BACKEND=gemini-api", content)
        self.assertIn("DELEGATION_BACKEND=gemini-cli", content)
        self.assertIn("GEMINI_API_KEY", content)


class TestClaudeCommand(unittest.TestCase):
    def test_create_claude_command_writes_delegate_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            create_claude_command(claude_dir)
            delegate_md = claude_dir / "commands" / "delegate.md"
            self.assertTrue(delegate_md.is_file())
            content = delegate_md.read_text(encoding="utf-8")
            self.assertIn("delegate_and_log.ps1", content)
            self.assertIn("$ARGUMENTS", content)

    def test_install_creates_claude_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_hooks(target_dir=tmpdir)
            delegate_md = Path(tmpdir) / ".claude" / "commands" / "delegate.md"
            self.assertTrue(delegate_md.is_file())

    def test_uninstall_removes_claude_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_hooks(target_dir=tmpdir)
            uninstall_hooks(target_dir=tmpdir)
            delegate_md = Path(tmpdir) / ".claude" / "commands" / "delegate.md"
            self.assertFalse(delegate_md.exists())


class TestDelegationCallerToken(unittest.TestCase):
    def test_install_writes_delegation_caller_env_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_hooks(target_dir=tmpdir)
            settings = json.loads(
                (Path(tmpdir) / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(settings.get("env", {}).get("DELEGATION_CALLER"), "claude")

    def test_uninstall_removes_delegation_caller_env_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_hooks(target_dir=tmpdir)
            uninstall_hooks(target_dir=tmpdir)
            settings = json.loads(
                (Path(tmpdir) / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("DELEGATION_CALLER", settings.get("env", {}))

    def test_revert_preserves_unrelated_env_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text(
                json.dumps({"env": {"DELEGATION_CALLER": "claude", "MY_VAR": "keep"}}),
                encoding="utf-8",
            )
            revert_claude_settings(claude_dir)
            settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("DELEGATION_CALLER", settings.get("env", {}))
            self.assertEqual(settings["env"]["MY_VAR"], "keep")


class TestAgentsMdContentGuard(unittest.TestCase):
    def test_install_preserves_claude_md_when_agents_md_has_content(self):
        """If AGENTS.md already has user content, CLAUDE.md must not be touched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            original_claude = "# Claude-only rules\nDo not share with Codex.\n"
            (project_dir / "CLAUDE.md").write_text(original_claude, encoding="utf-8")
            (project_dir / "AGENTS.md").write_text(
                "# Shared agent instructions\nBoth Claude and Codex see this.\n",
                encoding="utf-8",
            )

            import io
            from contextlib import redirect_stdout
            out = io.StringIO()
            with redirect_stdout(out):
                install_hooks(target_dir=tmpdir)

            # CLAUDE.md must be untouched
            self.assertEqual(
                (project_dir / "CLAUDE.md").read_text(encoding="utf-8"),
                original_claude,
            )
            # Preservation is expected and should not require --preserve-claude-md.
            self.assertNotIn("WARN", out.getvalue())
            self.assertIn("Preserved CLAUDE.md", out.getvalue())
            self.assertIn("AGENTS.md", out.getvalue())
            # Delegation block was still added to AGENTS.md
            agents_text = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn(AGENTS_MARKER_BEGIN, agents_text)

    def test_install_proceeds_normally_when_agents_md_is_empty(self):
        """Empty AGENTS.md must not trigger the guard."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "CLAUDE.md").write_text("# Rules\nKeep.\n", encoding="utf-8")
            (project_dir / "AGENTS.md").write_text("", encoding="utf-8")

            result = install_hooks(target_dir=tmpdir)

            self.assertEqual(result, 0)
            self.assertEqual(
                (project_dir / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n"
            )

    def test_reinstall_does_not_trigger_guard(self):
        """Re-running install after initial install must not produce spurious warnings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            install_hooks(target_dir=tmpdir)

            import io
            from contextlib import redirect_stdout
            out = io.StringIO()
            with redirect_stdout(out):
                result = install_hooks(target_dir=tmpdir)

            self.assertEqual(result, 0)
            self.assertNotIn("WARN", out.getvalue())


class TestNoUpdateFlag(unittest.TestCase):
    def test_no_update_errors_if_already_installed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_hooks(target_dir=tmpdir)
            with self.assertRaises(InstallError):
                install_hooks(target_dir=tmpdir, no_update=True)

    def test_no_update_succeeds_on_fresh_install(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = install_hooks(target_dir=tmpdir, no_update=True)
            self.assertEqual(result, 0)


class TestRevertClaudeSettings(unittest.TestCase):
    def test_revert_removes_delegation_guard_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            settings_path = claude_dir / "settings.json"
            settings_path.write_text(
                json.dumps({
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "pwsh -File .claude/hooks/delegation_guard.ps1"}],
                            },
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "echo keep"}],
                            },
                        ]
                    },
                    "theme": "dark",
                }),
                encoding="utf-8",
            )

            changed = revert_claude_settings(claude_dir)

            self.assertTrue(changed)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            commands = [
                hook["command"]
                for entry in settings["hooks"]["PreToolUse"]
                for hook in entry["hooks"]
            ]
            self.assertIn("echo keep", commands)
            self.assertFalse(any("delegation_guard" in c for c in commands))
            self.assertEqual(settings["theme"], "dark")  # unrelated keys preserved

    def test_revert_returns_false_when_nothing_to_revert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.json").write_text(
                json.dumps({"theme": "dark"}), encoding="utf-8"
            )
            self.assertFalse(revert_claude_settings(claude_dir))

    def test_uninstall_reverts_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_hooks(target_dir=tmpdir)
            settings_before = json.loads(
                (Path(tmpdir) / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            guard_cmds = [
                hook["command"]
                for entry in settings_before.get("hooks", {}).get("PreToolUse", [])
                for hook in entry.get("hooks", [])
                if "delegation_guard" in hook.get("command", "")
            ]
            self.assertTrue(guard_cmds, "install should have added delegation_guard hooks")

            uninstall_hooks(target_dir=tmpdir)

            settings_after = json.loads(
                (Path(tmpdir) / ".claude" / "settings.json").read_text(encoding="utf-8")
            )
            remaining = [
                hook["command"]
                for entry in settings_after.get("hooks", {}).get("PreToolUse", [])
                for hook in entry.get("hooks", [])
                if "delegation_guard" in hook.get("command", "")
            ]
            self.assertFalse(remaining)


class TestPreserveClaudeMd(unittest.TestCase):
    def test_install_preserves_hand_authored_claude_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            hand_authored = "@AGENTS.md\n\n# Claude-specific rules\nDo not rewrite this.\n"
            (project_dir / "CLAUDE.md").write_text(hand_authored, encoding="utf-8")

            result = install_hooks(target_dir=tmpdir, preserve_claude_md=True)

            self.assertEqual(result, 0)
            self.assertEqual(
                (project_dir / "CLAUDE.md").read_text(encoding="utf-8"),
                hand_authored,
                "CLAUDE.md must not be modified with --preserve-claude-md",
            )

    def test_verify_accepts_multi_line_claude_md_starting_with_agents_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            install_hooks(target_dir=tmpdir, preserve_claude_md=True)
            # verify_install with preserve mode should not raise
            verify_install(tmpdir, preserve_claude_md=True)


class TestUninstallLegacyHooks(unittest.TestCase):
    def test_uninstall_removes_legacy_direct_copy_hooks(self):
        """Uninstall should remove old-style direct-copy .py hooks too."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            install_hooks(target_dir=tmpdir)
            # Simulate legacy files dropped directly into .claude/hooks/
            for name in ("gemini_delegate.py", "pre_delegate.py", "post_delegate.py",
                         "analyze_metrics.py", "delegation_guard.py"):
                (project_dir / ".claude" / "hooks" / name).write_text("# legacy\n", encoding="utf-8")

            uninstall_hooks(target_dir=tmpdir)

            for name in ("gemini_delegate.py", "pre_delegate.py", "post_delegate.py",
                         "analyze_metrics.py", "delegation_guard.py"):
                self.assertFalse((project_dir / ".claude" / "hooks" / name).exists(),
                                 f"legacy hook {name} should have been removed")

    def test_uninstall_preserves_unrelated_hooks(self):
        """Uninstall must not touch hooks that are not delegation-managed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            install_hooks(target_dir=tmpdir)
            keep_hook = project_dir / ".claude" / "hooks" / "mempalace-hook.ps1"
            keep_hook.write_text("# unrelated hook\n", encoding="utf-8")

            uninstall_hooks(target_dir=tmpdir)

            self.assertTrue(keep_hook.exists(), "unrelated hook must survive uninstall")


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
            self.assertEqual(sum(".gemini-delegation/hooks/delegation_guard.ps1" in command for command in commands), 2)
            self.assertFalse(any("delegation_guard.py" in command for command in commands))

    def test_create_claude_settings_removes_stale_absolute_archive_hooks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir()
            settings_path = claude_dir / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {
                                    "matcher": ".*",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "powershell.exe",
                                            "args": [
                                                "-File",
                                                "C:\\Users\\User\\.claude\\hooks\\archive-jsonl.ps1",
                                            ],
                                        },
                                        {"type": "command", "command": "echo keep"},
                                    ],
                                }
                            ],
                            "SessionEnd": [
                                {
                                    "matcher": ".*",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "powershell.exe",
                                            "args": ["-File", "C:\\Users\\User\\.claude\\hooks\\archive-jsonl.ps1"],
                                        }
                                    ],
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            create_claude_settings(claude_dir)

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            serialized = json.dumps(settings)
            self.assertNotIn("archive-jsonl.ps1", serialized)
            self.assertIn("echo keep", serialized)
            self.assertNotIn("SessionEnd", settings.get("hooks", {}))

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


class TestCheckForUpdate(unittest.TestCase):
    def _make_urlopen(self, tag: str):
        payload = json.dumps({"tag_name": tag}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        return MagicMock(return_value=mock_resp)

    def test_prints_update_notice_when_newer_release_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "delegation_config.json"
            config_path.write_text(json.dumps({}), encoding="utf-8")

            with patch("urllib.request.urlopen", self._make_urlopen("v99.0.0")):
                with patch("builtins.print") as mock_print:
                    check_for_update(config_path)

            printed = " ".join(str(c) for c in (call.args[0] for call in mock_print.call_args_list))
            self.assertIn("[UPDATE]", printed)
            self.assertIn("v99.0.0", printed)

    def test_no_notice_when_already_on_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "delegation_config.json"
            config_path.write_text(json.dumps({}), encoding="utf-8")

            with patch("urllib.request.urlopen", self._make_urlopen("v0.0.1")):
                with patch("builtins.print") as mock_print:
                    check_for_update(config_path)

            printed = " ".join(str(c) for c in (call.args[0] for call in mock_print.call_args_list))
            self.assertNotIn("[UPDATE]", printed)

    def test_skips_check_within_24h(self):
        from datetime import timezone
        recent = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "delegation_config.json"
            config_path.write_text(
                json.dumps({"last_update_check": recent}), encoding="utf-8"
            )

            with patch("urllib.request.urlopen") as mock_urlopen:
                check_for_update(config_path)

            mock_urlopen.assert_not_called()

    def test_stores_last_check_and_tag_in_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "delegation_config.json"
            config_path.write_text(json.dumps({"installed_by": "test"}), encoding="utf-8")

            with patch("urllib.request.urlopen", self._make_urlopen("v99.0.0")):
                check_for_update(config_path)

            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIn("last_update_check", config)
            self.assertEqual(config["latest_release"], "v99.0.0")
            self.assertEqual(config["installed_by"], "test")  # unrelated keys preserved

    def test_silently_survives_network_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "delegation_config.json"
            config_path.write_text(json.dumps({}), encoding="utf-8")

            with patch("urllib.request.urlopen", side_effect=OSError("no network")):
                check_for_update(config_path)  # must not raise


if __name__ == "__main__":
    unittest.main()
