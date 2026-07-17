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

from agent_delegation import cli, guard, policy  # noqa: E402


class TestGlobalPolicy(unittest.TestCase):
    def test_default_policy_is_read_only(self):
        rendered = {tuple(item) for item in policy.DEFAULT_POLICY["command_prefixes"]}
        self.assertIn(("rg",), rendered)
        self.assertIn(("git", "diff"), rendered)
        self.assertNotIn(("git", "commit"), rendered)

    def test_run_scoped_extension_is_added(self):
        prefixes = policy.command_prefixes(policy.DEFAULT_POLICY, ["python -m pytest"])
        self.assertIn(["python", "-m", "pytest"], prefixes)

    def test_shell_extension_is_permanently_denied(self):
        with self.assertRaises(ValueError):
            policy.command_prefixes(policy.DEFAULT_POLICY, ["pwsh -File anything.ps1"])


class TestCommandGuard(unittest.TestCase):
    def test_allows_reviewed_prefix(self):
        allowed, _ = guard.is_allowed("git diff --stat", [["git", "diff"]])
        self.assertTrue(allowed)

    def test_denies_unlisted_command(self):
        allowed, _ = guard.is_allowed("git commit -m test", [["git", "diff"]])
        self.assertFalse(allowed)

    def test_denies_compound_command(self):
        allowed, _ = guard.is_allowed("rg needle .; Remove-Item file", [["rg"]])
        self.assertFalse(allowed)

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
        env = {cli.DEPTH_ENV: "1", cli.ALLOW_ENV: json.dumps([["rg"]])}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", StringIO(payload)):
                with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
                    self.assertEqual(guard.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["permissionOverrides"], ["command(rg needle .)"])


class TestGlobalCli(unittest.TestCase):
    def test_nested_delegation_is_rejected(self):
        with mock.patch.dict(os.environ, {cli.DEPTH_ENV: "1"}, clear=True):
            code = cli.main(["run", "do work"])
        self.assertEqual(code, 2)

    def test_install_creates_managed_and_local_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agy_root = str(Path(tmpdir) / "agy")
            env = {"AGENT_DELEGATION_HOME": tmpdir, "AGENT_DELEGATION_AGY_ROOT": agy_root}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(cli.main(["install", "--force"]), 0)
                self.assertTrue((Path(tmpdir) / "policy.json").is_file())
                self.assertTrue((Path(tmpdir) / "policy.local.json").is_file())
                hooks = json.loads((Path(agy_root) / "hooks.json").read_text(encoding="utf-8"))
                self.assertIn("agent-delegation-command-policy", hooks)

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
                with mock.patch.object(cli, "_invoke_runner", return_value=(0, "result\n")):
                    with mock.patch.object(cli, "_native_transcripts", return_value={}):
                        self.assertEqual(cli.main(args), 0)

            manifests = list(home.glob("runs/codex/repo/*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["depth"], 1)
            self.assertEqual(manifest["caller_extensions"], ["python -m pytest"])
            self.assertFalse(manifest["native_state_modified_by_agent_delegation"])
            exchange = Path(manifest["exchange_jsonl"])
            lines = exchange.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["text"], "result\n")


class TestBackendSafety(unittest.TestCase):
    def test_runner_contains_sandbox_and_no_bypass_flags(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "agent_delegation" / "runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"--sandbox"', source)
        forbidden = "--" + "yolo"
        skip = "--" + "skip-trust"
        self.assertNotIn(forbidden, source)
        self.assertNotIn(skip, source)


if __name__ == "__main__":
    unittest.main()
