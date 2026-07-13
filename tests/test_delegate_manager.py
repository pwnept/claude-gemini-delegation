"""
Offline unit tests for delegate_manager (async delegations + persistent registry).
Run with: python3 -m unittest discover tests
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import delegate_manager  # noqa: E402


class DelegateManagerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ,
            {"DELEGATION_LOG_ROOT": self._tmp.name, "DELEGATION_DISABLE_LOGS": "1"},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)

    def _fire_async(self, task="find where X lives", **spawn_patch_kwargs):
        out = io.StringIO()
        with mock.patch.object(delegate_manager, "_spawn_detached", return_value=4321):
            with redirect_stdout(out):
                code = delegate_manager.main(["async", task, "--context", "unit test"])
        self.assertEqual(code, 0)
        return out.getvalue().strip().splitlines()[0]


class TestAsyncOneshot(DelegateManagerCase):
    def test_async_registers_delegate_and_returns_id_immediately(self):
        delegate_id = self._fire_async()

        record = delegate_manager.load_record(delegate_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["mode"], "oneshot")
        self.assertEqual(record["status"], "busy")
        self.assertEqual(record["task"], "find where X lives")
        self.assertEqual(record["pid"], 4321)

    def test_run_oneshot_writes_condensed_result_and_done_marker(self):
        delegate_id = self._fire_async()

        fake = subprocess.CompletedProcess(
            args=["python"], returncode=0,
            stdout="Final answer body: X lives in src/x.py\n", stderr="",
        )
        with mock.patch.object(delegate_manager.subprocess, "run", return_value=fake):
            code = delegate_manager.main(["run-oneshot", delegate_id])

        self.assertEqual(code, 0)
        ddir = delegate_manager.delegate_dir(delegate_id)
        self.assertTrue((ddir / "done").exists())
        result = (ddir / "result.md").read_text(encoding="utf-8")
        self.assertIn("status: done", result)
        self.assertIn("X lives in src/x.py", result)
        self.assertEqual(delegate_manager.load_record(delegate_id)["status"], "done")

    def test_run_oneshot_timeout_is_reported_in_done_marker(self):
        delegate_id = self._fire_async()

        fake = subprocess.CompletedProcess(
            args=["python"], returncode=1, stdout="",
            stderr="Gemini 3.5 Flash (High): Timed out (idle>60s or >600s total)",
        )
        with mock.patch.object(delegate_manager.subprocess, "run", return_value=fake):
            code = delegate_manager.main(["run-oneshot", delegate_id])

        self.assertEqual(code, 1)
        ddir = delegate_manager.delegate_dir(delegate_id)
        done_text = (ddir / "done").read_text(encoding="utf-8")
        self.assertIn("status: timeout", done_text)

    def test_wait_exit_codes_encode_done_warn_and_timeout(self):
        delegate_id = self._fire_async()
        ddir = delegate_manager.delegate_dir(delegate_id)

        # waiter timeout while the host is still alive and silent
        host_lock = delegate_manager.FileLock(ddir / "alive.lock")
        self.assertTrue(host_lock.acquire())
        try:
            code = delegate_manager.main(["wait", delegate_id, "--timeout-seconds", "1"])
        finally:
            host_lock.release()
        self.assertEqual(code, 3)

        # host died without writing a done marker -> exit 4 (report honestly).
        # Backdate created_at past the startup grace — a fresh record is
        # deliberately not declared dead while the detached child boots.
        record = delegate_manager.load_record(delegate_id)
        record["created_at"] = record["created_at"] - delegate_manager.HOST_STARTUP_GRACE - 1
        delegate_manager.save_record(record)
        code = delegate_manager.main(["wait", delegate_id, "--timeout-seconds", "5"])
        self.assertEqual(code, 4)

        # warn marker appearing mid-wait -> exit 2 (soft threshold is the wake)
        host_lock = delegate_manager.FileLock(ddir / "alive.lock")
        self.assertTrue(host_lock.acquire())
        try:
            def _warn_appears(_seconds):
                (ddir / "warn").write_text("soft-threshold\n", encoding="utf-8")

            with mock.patch.object(delegate_manager.time, "sleep", side_effect=_warn_appears):
                code = delegate_manager.main(["wait", delegate_id, "--timeout-seconds", "30"])
        finally:
            host_lock.release()
        self.assertEqual(code, 2)

        # done marker -> exit 0 and status line printed
        (ddir / "done").write_text("status: done (exit 0, 12s)\n", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = delegate_manager.main(["wait", delegate_id, "--timeout-seconds", "5"])
        self.assertEqual(code, 0)
        self.assertIn("status: done", out.getvalue())

    def test_wait_unknown_id_is_exit_4(self):
        code = delegate_manager.main(["wait", "nope-000000-abcdef", "--timeout-seconds", "1"])
        self.assertEqual(code, 4)


class TestRegistryAndSteering(DelegateManagerCase):
    def _make_persistent_record(self, delegate_id="dlg-test-000001", **overrides):
        record = {
            "id": delegate_id,
            "mode": "persistent",
            "status": "idle",
            "workspace": "C:\\ws",
            "profile": "default",
            "model": "Gemini 3.5 Flash (High)",
            "created_at": delegate_manager.time.time(),
            "last_activity": delegate_manager.time.time(),
            "turn_count": 0,
            "originating_session": "session-a",
            "last_attached_session": "session-a",
        }
        record.update(overrides)
        (delegate_manager.delegate_dir(delegate_id) / "steer").mkdir(parents=True, exist_ok=True)
        delegate_manager.save_record(record)
        return record

    def test_steer_refuses_dead_delegate_so_caller_redispatches(self):
        record = self._make_persistent_record()
        with mock.patch.object(delegate_manager, "HOST_STARTUP_GRACE", 0):
            code = delegate_manager.main(["steer", record["id"], "next question"])
        self.assertEqual(code, 4)
        self.assertEqual(delegate_manager.load_record(record["id"])["status"], "dead")

    def test_steer_single_writer_lock_blocks_concurrent_steer(self):
        record = self._make_persistent_record()
        ddir = delegate_manager.delegate_dir(record["id"])
        host_lock = delegate_manager.FileLock(ddir / "alive.lock")
        self.assertTrue(host_lock.acquire())  # simulate a live host
        first_writer = delegate_manager.FileLock(ddir / "steer.lock")
        self.assertTrue(first_writer.acquire())  # simulate a session mid-steer
        try:
            code = delegate_manager.main(["steer", record["id"], "second writer"])
            self.assertEqual(code, 5)
        finally:
            first_writer.release()
            host_lock.release()

    def test_steer_from_second_session_updates_last_attached(self):
        record = self._make_persistent_record()
        ddir = delegate_manager.delegate_dir(record["id"])
        host_lock = delegate_manager.FileLock(ddir / "alive.lock")
        self.assertTrue(host_lock.acquire())
        try:
            def _answer_when_asked(_seconds):
                prompts = sorted((ddir / "steer").glob("*.prompt"))
                if prompts:
                    stem = prompts[-1].stem
                    (ddir / "steer" / f"{stem}.response").write_text(
                        "status: done (turn 1)\n\nanswer body", encoding="utf-8"
                    )

            out = io.StringIO()
            with mock.patch.object(delegate_manager.time, "sleep", side_effect=_answer_when_asked):
                with redirect_stdout(out):
                    code = delegate_manager.main(
                        ["steer", record["id"], "continue the survey", "--session-id", "session-b"]
                    )
            self.assertEqual(code, 0)
            self.assertIn("answer body", out.getvalue())
            updated = delegate_manager.load_record(record["id"])
            self.assertEqual(updated["last_attached_session"], "session-b")
            self.assertEqual(updated["originating_session"], "session-a")
        finally:
            host_lock.release()

    def test_list_reports_dead_host_honestly(self):
        record = self._make_persistent_record(status="idle")
        out = io.StringIO()
        with redirect_stdout(out):
            code = delegate_manager.main(["list", "--json"])
        self.assertEqual(code, 0)
        import json
        entries = json.loads(out.getvalue())
        entry = next(e for e in entries if e["id"] == record["id"])
        self.assertEqual(entry["status"], "dead")
        self.assertFalse(entry["alive"])

    def test_list_prunes_long_dead_delegates(self):
        old = delegate_manager.time.time() - delegate_manager.MAX_AGE_SECONDS - 60
        record = self._make_persistent_record(created_at=old, last_activity=old, status="done")
        with redirect_stdout(io.StringIO()):
            delegate_manager.main(["list"])
        self.assertFalse(delegate_manager.delegate_dir(record["id"]).exists())

    def test_read_returns_latest_condensed_response(self):
        record = self._make_persistent_record()
        steer_dir = delegate_manager.delegate_dir(record["id"]) / "steer"
        (steer_dir / "0001.response").write_text("status: done\n\nfirst", encoding="utf-8")
        (steer_dir / "0002.response").write_text("status: done\n\nsecond", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = delegate_manager.main(["read", record["id"]])
        self.assertEqual(code, 0)
        self.assertIn("second", out.getvalue())
        self.assertNotIn("first", out.getvalue())


class TestAnswerDetection(unittest.TestCase):
    """The per-turn nonce marker must never be matched inside the prompt echo."""

    def test_echo_of_instructions_cannot_false_positive(self):
        nonce = "4f2a01"
        marker = delegate_manager._turn_marker(nonce)
        # The full injected prompt (what the TUI echoes and redraws, in any
        # wrapping) must not contain the assembled token anywhere.
        prompt = delegate_manager._interactive_prompt("default", nonce, "Where is X?")
        self.assertNotIn(marker, prompt)
        # Even a pathological hard-wrap of the echo yields no match.
        wrapped = "\n".join(prompt[i:i + 40] for i in range(0, len(prompt), 40))
        self.assertIsNone(delegate_manager._find_answer(wrapped, marker))

    def test_marker_line_yields_remainder(self):
        marker = delegate_manager._turn_marker("4f2a01")
        output = f"working\nsearching files\n{marker}\nhooks/gemini_delegate.py\n"
        self.assertEqual(
            delegate_manager._find_answer(output, marker),
            "hooks/gemini_delegate.py",
        )

    def test_marker_with_tui_decoration_still_matches(self):
        marker = delegate_manager._turn_marker("4f2a01")
        output = f"working\n● {marker}\n  the answer body\n"
        self.assertEqual(
            delegate_manager._find_answer(output, marker), "the answer body"
        )

    def test_marker_shredded_by_tui_repaints_still_matches(self):
        # Incremental TUI repaints can split the token across paint fragments
        # with whole chrome frames in between (observed live: "DELEGATION-A"
        # ... spinner/tip/input-box lines ... "NSWER-6b0f00").
        marker = delegate_manager._turn_marker("6b0f00")
        output = (
            "working\n"
            "  DELEGATION-A\n"
            "⣾  Generating...\n"
            "└ Tip: When reviewing a file edit, press f to see the full diff.\n"
            "\n"
            ">\n"
            "────────────────────────────\n"
            "esc to cancelGemini 3.5 Flash (High)\n"
            "NSWER-6b0f00\n"
            "  hooks/gemini_delegate.py\n"
        )
        self.assertEqual(
            delegate_manager._find_answer(output, marker),
            "hooks/gemini_delegate.py",
        )

    def test_marker_with_overlapping_repaint_duplication_matches(self):
        # Overlapping repaints can DUPLICATE boundary characters (observed
        # live: "ANSWER-0ea84" then "4a" for nonce 0ea84a — the '4' painted
        # twice). Each marker char may repeat across fragments.
        marker = delegate_manager._turn_marker("0ea84a")
        output = (
            "working\n"
            "  DELEGATION-\n"
            "⣾  Generating...\n"
            "ANSWER-0ea84\n"
            "\n"
            "\t\t\t4a\n"
            "  gemini_delegate.py\n"
        )
        self.assertEqual(
            delegate_manager._find_answer(output, marker),
            "gemini_delegate.py",
        )

    def test_clean_answer_strips_tui_chrome(self):
        body = (
            "hooks/gemini_delegate.py\n"
            "⣽  Working...\n"
            "└ Tip: Use /context to see what files are in the conversation.\n"
            "────────────────────────────\n"
            ">\n"
            "esc to cancel                    Gemini 3.5 Flash (High)\n"
            "tests/test_gemini_delegate.py\n"
        )
        self.assertEqual(
            delegate_manager._clean_answer(body),
            "hooks/gemini_delegate.py\ntests/test_gemini_delegate.py",
        )

    def test_marker_without_body_returns_none(self):
        marker = delegate_manager._turn_marker("4f2a01")
        self.assertIsNone(delegate_manager._find_answer(f"working\n{marker}\n", marker))


if __name__ == "__main__":
    unittest.main()
