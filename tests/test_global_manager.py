import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_delegation import cli, manager, policy, runner  # noqa: E402


class TestAgyValidationGate(unittest.TestCase):
    def test_managed_policy_disables_unvalidated_agy(self):
        self.assertFalse(policy.DEFAULT_POLICY["agy_print_mode_enabled"])

    def test_runner_rejects_direct_unvalidated_agy(self):
        argv = ["agent-delegation-runner", "--backend", "agy", "map files"]
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(runner.main(), 2)

    def test_alternate_backend_remains_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            argv = [
                "agent-delegation-runner",
                "--backend",
                "gemini-api",
                "--agent-dir",
                tmpdir,
                "map files",
            ]
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(runner, "run_api_backend", return_value=0):
                        self.assertEqual(runner.main(), 0)


class TestGlobalManagerCli(unittest.TestCase):
    def test_manager_commands_are_public(self):
        parser = cli.build_parser()
        subcommands = parser._subparsers._group_actions[0].choices
        for command in ("async", "wait", "spawn", "steer", "read", "list", "stop"):
            self.assertIn(command, subcommands)

    def test_async_is_fail_closed_until_agy_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "repo"
            home = root / "home"
            workspace.mkdir()
            subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            with mock.patch.dict(os.environ, {"AGENT_DELEGATION_HOME": str(home)}, clear=False):
                with mock.patch.object(manager, "main") as manager_main:
                    self.assertEqual(cli.main(["async", "map files", "--workspace", str(workspace)]), 2)
            manager_main.assert_not_called()

    def test_async_passes_policy_and_depth_to_detached_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "repo"
            home = root / "home"
            workspace.mkdir()
            home.mkdir()
            subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            (home / "policy.local.json").write_text(
                json.dumps({"schema": 1, "agy_print_mode_enabled": True}) + "\n",
                encoding="utf-8",
            )
            captured = {}

            def fake_manager_main(argv):
                captured["argv"] = argv
                captured["validated"] = os.environ.get("AGENT_DELEGATION_AGY_VALIDATED")
                captured["depth"] = os.environ.get(cli.DEPTH_ENV)
                captured["prefixes"] = json.loads(os.environ[cli.ALLOW_ENV])
                captured["cwd"] = str(Path.cwd())
                return 0

            env = {"AGENT_DELEGATION_HOME": str(home)}
            args = [
                "async",
                "map files",
                "--workspace",
                str(workspace),
                "--caller",
                "codex",
                "--allow-command",
                "python -m pytest",
            ]
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(manager, "main", side_effect=fake_manager_main):
                    self.assertEqual(cli.main(args), 0)

            self.assertEqual(captured["argv"][0:2], ["async", "map files"])
            self.assertEqual(captured["validated"], "1")
            self.assertIsNone(captured["depth"])
            self.assertIn(["python", "-m", "pytest"], captured["prefixes"])
            self.assertEqual(captured["cwd"], str(workspace.resolve()))


class TestPersistentManagerSafety(unittest.TestCase):
    def test_detached_children_are_depth_one(self):
        args = Namespace()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "host.log"
            fake_proc = mock.Mock(pid=1234)
            with mock.patch.object(subprocess, "Popen", return_value=fake_proc) as popen:
                self.assertEqual(manager._spawn_detached(["worker"], log_path), 1234)
        self.assertEqual(popen.call_args.kwargs["env"][cli.DEPTH_ENV], "1")

    def test_persistent_host_uses_sandboxed_plan_mode(self):
        source = Path(manager.__file__).read_text(encoding="utf-8")
        self.assertIn('"--mode",\n            "plan",\n            "--sandbox"', source)
        self.assertNotIn("dangerously-skip-permissions", source)
        self.assertNotIn('"--yolo"', source)

    def test_archive_writes_hash_verified_manifest_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            record = {"id": "dlg-test", "native_before": {"native.jsonl": [1, 2]}}
            archived = [{"source": "native.jsonl", "sha256": "abc"}]
            with mock.patch.dict(os.environ, {"AGENT_DELEGATION_HOME": str(root)}, clear=False):
                with mock.patch.object(cli, "_archive_native_transcripts", return_value=archived):
                    manager._archive_native(record, root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["native_transcripts"], archived)
            self.assertFalse(manifest["native_state_modified_by_agent_delegation"])


if __name__ == "__main__":
    unittest.main()
