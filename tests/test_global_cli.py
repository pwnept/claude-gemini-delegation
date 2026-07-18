import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from io import StringIO
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_delegation import cli, guard, policy, runner  # noqa: E402


class TestGlobalPolicy(unittest.TestCase):
    def test_default_policy_is_read_only(self):
        rendered = {tuple(item) for item in policy.DEFAULT_POLICY["command_prefixes"]}
        self.assertIn(("rg",), rendered)
        self.assertIn(("git", "--no-pager", "diff", "--no-ext-diff", "--no-textconv"), rendered)
        self.assertIn(
            ("git", "--no-pager", "log", "--no-ext-diff", "--no-textconv", "--no-show-signature"),
            rendered,
        )
        self.assertNotIn(("git", "commit"), rendered)

    def test_run_scoped_extension_is_added(self):
        prefixes = policy.command_prefixes(policy.DEFAULT_POLICY, ["python -m pytest"])
        self.assertIn(["python", "-m", "pytest"], prefixes)

    def test_shell_extension_is_permanently_denied(self):
        with self.assertRaises(ValueError):
            policy.command_prefixes(policy.DEFAULT_POLICY, ["pwsh -File anything.ps1"])

    def test_write_git_extension_is_permanently_denied(self):
        with self.assertRaises(ValueError):
            policy.command_prefixes(policy.DEFAULT_POLICY, ["git commit"])

    def test_execution_bearing_git_forms_are_permanently_denied(self):
        for prefix in ("git diff", "git show", "git log", "git grep"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    policy.command_prefixes(policy.DEFAULT_POLICY, [prefix])


class TestCommandGuard(unittest.TestCase):
    def test_allows_reviewed_prefix(self):
        allowed, _ = guard.is_allowed(
            "git --no-pager diff --no-ext-diff --no-textconv --stat",
            [["git", "--no-pager", "diff", "--no-ext-diff", "--no-textconv"]],
        )
        self.assertTrue(allowed)

    def test_denies_unlisted_command(self):
        allowed, _ = guard.is_allowed("git commit -m test", [["git", "diff"]])
        self.assertFalse(allowed)

    def test_denies_compound_command(self):
        allowed, _ = guard.is_allowed("rg needle .; Remove-Item file", [["rg"]])
        self.assertFalse(allowed)

    def test_denies_write_or_execution_flags(self):
        self.assertFalse(guard.is_allowed("git diff --output=result.txt", [["git", "diff"]])[0])
        self.assertFalse(guard.is_allowed("rg --pre helper needle .", [["rg"]])[0])
        self.assertFalse(guard.is_allowed("fd pattern --exec Remove-Item", [["fd"]])[0])
        self.assertFalse(guard.is_allowed("git diff --ext-diff", [["git", "diff"]])[0])
        self.assertFalse(guard.is_allowed("git show --textconv", [["git", "show"]])[0])
        self.assertFalse(guard.is_allowed("git log --show-signature", [["git", "log"]])[0])
        self.assertFalse(
            guard.is_allowed("git grep --open-files-in-pager=calc needle", [["git", "grep"]])[0]
        )

    def test_guard_is_inactive_for_main_caller(self):
        with mock.patch.dict(os.environ, {cli.DEPTH_ENV: "0"}, clear=True):
            self.assertEqual(guard.main(), 0)

    def test_guard_blocks_depth_one_unlisted_command(self):
        payload = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": "git commit -m test"}}})
        env = {
            cli.DEPTH_ENV: "1",
            cli.ALLOW_ENV: json.dumps([["git", "diff"]]),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", StringIO(payload)):
                with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
                    self.assertEqual(guard.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["decision"], "deny")

    def test_guard_returns_exact_temporary_grant(self):
        payload = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": "rg needle ."}}})
        env = {
            cli.DEPTH_ENV: "1",
            cli.ALLOW_ENV: json.dumps([["rg"]]),
            cli.WORKSPACE_ENV: str(Path.cwd()),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", StringIO(payload)):
                with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
                    self.assertEqual(guard.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["permissionOverrides"], ["command(rg needle .)"])

    def test_guard_unwraps_agy_serialized_command(self):
        payload = json.dumps(
            {"toolCall": {"name": "run_command", "args": {"CommandLine": '"rg DEPTH_ENV src"'}}}
        )
        env = {
            cli.DEPTH_ENV: "1",
            cli.ALLOW_ENV: json.dumps([["rg"]]),
            cli.WORKSPACE_ENV: str(Path.cwd()),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", StringIO(payload)):
                with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
                    self.assertEqual(guard.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["permissionOverrides"], ['command("rg DEPTH_ENV src")'])

    def test_wrapped_commands_still_enforce_denials(self):
        prefixes = [["rg"]]
        self.assertFalse(guard.is_allowed('"Get-Date"', prefixes)[0])
        self.assertFalse(guard.is_allowed('"rg needle .; Get-Date"', prefixes)[0])
        self.assertFalse(guard.is_allowed('"rg needle . > result.txt"', prefixes)[0])
        self.assertFalse(guard.is_allowed('"rg needle .', prefixes)[0])

    def test_windows_command_metacharacters_are_denied(self):
        prefixes = [["rg"]]
        commands = (
            "rg needle . & Get-Date",
            "rg needle . ^& Get-Date",
            "rg %PATTERN% src",
            "rg !PATTERN! src",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(guard.is_allowed(command, prefixes)[0])
                self.assertFalse(guard.is_allowed(json.dumps(command), prefixes)[0])

    def test_wrapped_command_preserves_inner_quoted_argument(self):
        command = json.dumps('rg "two words" src')
        self.assertTrue(guard.is_allowed(command, [["rg"]])[0])

    def test_guard_denies_missing_workspace(self):
        payload = json.dumps(
            {"toolCall": {"name": "run_command", "args": {"CommandLine": "rg needle ."}}}
        )
        env = {cli.DEPTH_ENV: "1", cli.ALLOW_ENV: json.dumps([["rg"]])}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", StringIO(payload)):
                with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
                    self.assertEqual(guard.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue())["decision"], "deny")

    def test_guard_confines_paths_to_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            outside = Path(tmpdir) / "outside.txt"
            root.mkdir()
            outside.write_text("secret", encoding="utf-8")
            self.assertTrue(guard.is_allowed("rg needle .", [["rg"]], str(root))[0])
            allowed, reason = guard.is_allowed(
                f'rg needle "{outside}"', [["rg"]], str(root)
            )
            self.assertFalse(allowed)
            self.assertIn("escapes delegated workspace", reason)
            self.assertFalse(guard.is_allowed("rg needle ..", [["rg"]], str(root))[0])

    def test_guard_denies_powershell_quoted_and_list_path_escapes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            prefixes = [["Get-Content"]]
            self.assertFalse(
                guard.is_allowed("Get-Content '..\\secret.txt'", prefixes, str(root))[0]
            )
            self.assertFalse(
                guard.is_allowed(
                    "Get-Content .\\inside.txt,..\\secret.txt", prefixes, str(root)
                )[0]
            )

    def test_guard_denies_follow_flags_and_wildcards(self):
        workspace = str(Path.cwd())
        for command in (
            "rg --follow needle .",
            "rg -L needle .",
            "rg -Luu needle .",
            "Get-ChildItem -FollowSymlink .",
            "Get-ChildItem -FollowSymlink:$true .",
            "Get-Content *\\secret.txt",
            "rg needle src/*",
            "rg -efoo src/*",
            "fd pattern -xRemove-Item src",
            "fd --base-directory .. pattern",
            "fd --search-path=.. pattern",
        ):
            with self.subTest(command=command):
                prefix = [[command.split()[0]]]
                self.assertFalse(guard.is_allowed(command, prefix, workspace)[0])

    def test_guard_allows_regex_wildcards_but_not_path_wildcards(self):
        workspace = str(Path.cwd())
        for command in (
            'rg "foo.*bar" src',
            'rg "what?" src',
            'rg "[A-Z]" src',
            'rg -e "foo.*bar" src',
        ):
            with self.subTest(command=command):
                self.assertTrue(guard.is_allowed(command, [["rg"]], workspace)[0])

    def test_guard_confines_file_valued_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "repo"
            outside = base / "outside.txt"
            root.mkdir()
            outside.write_text("needle", encoding="utf-8")
            commands = (
                f'rg -f "{outside}" .',
                f'rg --file="{outside}" .',
                f'rg --ignore-file="{outside}" needle .',
                f'fd --ignore-file="{outside}" pattern .',
                f'Select-String -Path:"{outside}" -Pattern needle',
                f'Select-String -Path="{outside}" -Pattern needle',
                f'Select-String -Path "{outside}" -Pattern needle',
                f'Select-String -LiteralPath "{outside}" -Pattern needle',
            )
            for command in commands:
                with self.subTest(command=command):
                    prefix = [[command.split()[0]]]
                    self.assertFalse(guard.is_allowed(command, prefix, str(root))[0])

    def test_guard_resolves_real_symlink_before_allowing_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "repo"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            link = root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            self.assertFalse(
                guard.is_allowed(
                    "Get-Content linked\\secret.txt", [["Get-Content"]], str(root)
                )[0]
            )

    def test_isolated_python_does_not_search_hostile_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            hostile = workspace / "agent_delegation"
            hostile.mkdir()
            marker = workspace / "shadowed.txt"
            (hostile / "__init__.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('shadowed')\n",
                encoding="utf-8",
            )
            payload = json.dumps(
                {"toolCall": {"name": "run_command", "args": {"CommandLine": "rg needle ."}}}
            )
            env = {
                **os.environ,
                cli.DEPTH_ENV: "1",
                cli.ALLOW_ENV: json.dumps([["rg"]]),
                cli.WORKSPACE_ENV: str(workspace),
            }
            result = subprocess.run(
                cli._guard_argv(),
                cwd=workspace,
                env=env,
                input=payload,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout)["decision"], "allow")
            self.assertFalse(marker.exists())


class TestGlobalCli(unittest.TestCase):
    @staticmethod
    def _enable_agy_for_offline_test(home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        (home / "policy.local.json").write_text(
            json.dumps({"schema": 1, "agy_print_mode_enabled": True}) + "\n",
            encoding="utf-8",
        )

    def test_nested_delegation_is_rejected(self):
        with mock.patch.dict(os.environ, {cli.DEPTH_ENV: "1"}, clear=True):
            code = cli.main(["run", "do work"])
        self.assertEqual(code, 2)

    def test_install_creates_managed_and_local_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agy_root = str(Path(tmpdir) / "agy")
            agy_config_root = str(Path(tmpdir) / "agy-config")
            env = {
                "AGENT_DELEGATION_HOME": tmpdir,
                "AGENT_DELEGATION_AGY_ROOT": agy_root,
                "AGENT_DELEGATION_AGY_CONFIG_ROOT": agy_config_root,
            }
            config_hooks = Path(agy_config_root) / "hooks.json"
            config_hooks.parent.mkdir(parents=True)
            config_hooks.write_text(json.dumps({"existing-hook": {"enabled": True}}), encoding="utf-8")
            old_hooks = Path(agy_root) / "hooks.json"
            old_hooks.parent.mkdir(parents=True)
            old_hooks.write_text('{"legacy": true}', encoding="utf-8")
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(cli.main(["install", "--force"]), 0)
                self.assertTrue((Path(tmpdir) / "policy.json").is_file())
                self.assertTrue((Path(tmpdir) / "policy.local.json").is_file())
                hooks = json.loads(config_hooks.read_text(encoding="utf-8"))
                self.assertIn("agent-delegation-command-policy", hooks)
                self.assertIn("existing-hook", hooks)
                command = hooks["agent-delegation-command-policy"]["PreToolUse"][0]["hooks"][0]["command"]
                self.assertIn(str(Path(sys.executable).resolve()), command)
                self.assertIn("-I -c", command)
                self.assertIn(repr(str(Path(cli.__file__).resolve().parent.parent)), command)
                self.assertNotEqual(command, "agent-delegation guard")
                backups = list(config_hooks.parent.glob("hooks.json.backup-*"))
                self.assertEqual(len(backups), 1)
                self.assertIn("existing-hook", json.loads(backups[0].read_text(encoding="utf-8")))
                self.assertEqual(old_hooks.read_text(encoding="utf-8"), '{"legacy": true}')

    def test_hook_install_replace_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            hooks = root / "hooks.json"
            original = '{"existing-hook": {"enabled": true}}'
            hooks.write_text(original, encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"AGENT_DELEGATION_AGY_CONFIG_ROOT": str(root)}, clear=False
            ):
                with mock.patch.object(cli.os, "replace", side_effect=OSError("replace failed")):
                    with self.assertRaises(OSError):
                        cli._install_agy_hook()
            self.assertEqual(hooks.read_text(encoding="utf-8"), original)
            self.assertEqual(list(root.glob("hooks.json.tmp-*")), [])

    def test_install_rejects_non_object_agy_hooks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_root = Path(tmpdir) / "agy-config"
            config_root.mkdir()
            hooks = config_root / "hooks.json"
            hooks.write_text("[]", encoding="utf-8")
            env = {
                "AGENT_DELEGATION_HOME": str(Path(tmpdir) / "home"),
                "AGENT_DELEGATION_AGY_CONFIG_ROOT": str(config_root),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(cli.main(["install", "--force"]), 2)
            self.assertEqual(hooks.read_text(encoding="utf-8"), "[]")

    def test_disable_and_enable_use_local_git_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init", tmpdir], check=True, capture_output=True)
            self.assertEqual(cli.main(["disable", tmpdir]), 0)
            self.assertFalse(cli._git_enabled(Path(tmpdir)))
            self.assertEqual(cli.main(["enable", tmpdir]), 0)
            self.assertTrue(cli._git_enabled(Path(tmpdir)))

    def test_offline_run_writes_jsonl_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "repo"
            home = root / "home"
            workspace.mkdir()
            subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            self._enable_agy_for_offline_test(home)
            args = [
                "run",
                "map files",
                "--workspace",
                str(workspace),
                "--caller",
                "codex",
                "--allow-command",
                "python -m pytest",
            ]
            env = {"AGENT_DELEGATION_HOME": str(home)}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(cli, "_invoke_runner", return_value=(0, "result\n", "", "model-a")):
                    with mock.patch.object(cli, "_native_transcripts", return_value={}):
                        self.assertEqual(cli.main(args), 0)

            manifests = list(home.glob("runs/codex/repo/*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["depth"], 1)
            self.assertEqual(manifest["caller_extensions"], ["python -m pytest"])
            self.assertEqual(manifest["worker_model"], "model-a")
            self.assertFalse(manifest["native_state_modified_by_agent_delegation"])
            exchange = Path(manifest["exchange_jsonl"])
            lines = exchange.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["text"], "result\n")

    def test_nonzero_runner_stderr_and_attempted_model_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "repo"
            home = root / "home"
            workspace.mkdir()
            subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            self._enable_agy_for_offline_test(home)
            args = ["run", "map files", "--workspace", str(workspace), "--caller", "codex"]
            with mock.patch.dict(os.environ, {"AGENT_DELEGATION_HOME": str(home)}, clear=False):
                with mock.patch.object(
                    cli, "_invoke_runner", return_value=(7, "", "authentication failed\n", "model-b")
                ):
                    with mock.patch.object(cli, "_native_transcripts", return_value={}):
                        self.assertEqual(cli.main(args), 7)

            manifest_path = next(home.glob("runs/codex/repo/*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["worker_model"], "model-b")
            self.assertEqual(manifest["runner_stderr"], "authentication failed\n")
            response_record = json.loads(
                Path(manifest["exchange_jsonl"]).read_text(encoding="utf-8").splitlines()[1]
            )
            self.assertEqual(response_record["stderr"], "authentication failed\n")

    def test_invoke_runner_tees_stderr_live(self):
        args = Namespace(
            backend="agy",
            profile="balanced",
            timeout=2,
            idle_timeout=1,
            model=None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            live = StringIO()
            with mock.patch.object(runner, "main", side_effect=lambda: print("trying model", file=sys.stderr) or 7):
                with mock.patch("sys.stderr", live):
                    code, _, captured, _ = cli._invoke_runner(args, "task", Path(tmpdir), "codex")
        self.assertEqual(code, 7)
        self.assertEqual(captured, "trying model\n")
        self.assertEqual(live.getvalue(), "trying model\n")

    def test_runner_failure_still_writes_manifest_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "repo"
            home = root / "home"
            workspace.mkdir()
            subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            self._enable_agy_for_offline_test(home)
            args = ["run", "map files", "--workspace", str(workspace), "--caller", "codex"]
            with mock.patch.dict(os.environ, {"AGENT_DELEGATION_HOME": str(home)}, clear=False):
                with mock.patch.object(cli, "_invoke_runner", side_effect=RuntimeError("worker crashed")):
                    with mock.patch.object(cli, "_native_transcripts", return_value={}):
                        self.assertEqual(cli.main(args), 2)

            manifest_path = next(home.glob("runs/codex/repo/*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["exit_code"], 2)
            self.assertIn("worker crashed", manifest["run_error"])
            self.assertTrue(Path(manifest["exchange_jsonl"]).is_file())

    def test_archive_failure_is_recorded_without_hiding_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "repo"
            home = root / "home"
            workspace.mkdir()
            subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            self._enable_agy_for_offline_test(home)
            args = ["run", "map files", "--workspace", str(workspace), "--caller", "codex"]
            with mock.patch.dict(os.environ, {"AGENT_DELEGATION_HOME": str(home)}, clear=False):
                with mock.patch.object(cli, "_invoke_runner", return_value=(0, "result\n", "", "model-a")):
                    with mock.patch.object(cli, "_native_transcripts", return_value={}):
                        with mock.patch.object(
                            cli, "_archive_native_transcripts", side_effect=OSError("copy failed")
                        ):
                            self.assertEqual(cli.main(args), 2)

            manifest_path = next(home.glob("runs/codex/repo/*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("copy failed", manifest["native_archive_error"])

    def test_keyboard_interrupt_still_writes_manifest_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "repo"
            home = root / "home"
            workspace.mkdir()
            subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            self._enable_agy_for_offline_test(home)
            args = ["run", "map files", "--workspace", str(workspace), "--caller", "codex"]
            with mock.patch.dict(os.environ, {"AGENT_DELEGATION_HOME": str(home)}, clear=False):
                with mock.patch.object(cli, "_invoke_runner", side_effect=KeyboardInterrupt()):
                    with mock.patch.object(cli, "_native_transcripts", return_value={}):
                        self.assertEqual(cli.main(args), 130)

            manifest_path = next(home.glob("runs/codex/repo/*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["exit_code"], 130)
            self.assertIn("KeyboardInterrupt", manifest["run_error"])
            self.assertTrue(Path(manifest["exchange_jsonl"]).is_file())


class TestBackendSafety(unittest.TestCase):
    def test_gemini_cli_prompt_is_never_passed_to_a_shell(self):
        prompt = "inspect & whoami"
        process = mock.Mock()
        process.stdout = StringIO("ok\n")
        process.stderr = StringIO("")
        process.wait.return_value = 0
        process.returncode = 0
        process.pid = 1234
        with mock.patch.object(runner, "_gemini_cli_argv_prefix", return_value=["node", "gemini.js"]):
            with mock.patch.object(runner.subprocess, "Popen", return_value=process) as popen:
                result = runner.run_gemini_cli("model", prompt, 2)
        self.assertEqual(result.returncode, 0)
        command = popen.call_args.args[0]
        self.assertIn(prompt, command)
        self.assertIs(popen.call_args.kwargs["shell"], False)

    def test_failed_attempt_records_model(self):
        args = Namespace(
            no_state=True,
            idle_timeout_seconds=1,
            timeout_seconds=2,
            cooldown_seconds=300,
            show_model=False,
            no_save=True,
        )
        attempt = mock.Mock(return_value=subprocess.CompletedProcess([], 7, stdout="", stderr="failed"))
        self.assertEqual(runner.run_with_fallback(["model-b"], Path("unused"), args, attempt), 7)
        self.assertEqual(runner.LAST_MODEL_USED, "model-b")

    def test_runner_contains_sandbox_and_no_bypass_flags(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "agent_delegation" / "runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"--sandbox"', source)
        forbidden = "--" + "yolo"
        skip = "--" + "skip-trust"
        self.assertNotIn(forbidden, source)
        self.assertNotIn(skip, source)

    def test_windows_pty_exit_status_is_preserved(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "agent_delegation" / "runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("pty.get_exitstatus()", source)
        self.assertIn("returncode=exit_status", source)


if __name__ == "__main__":
    unittest.main()
