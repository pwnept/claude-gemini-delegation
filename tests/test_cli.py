import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gemini_delegation import cli  # noqa: E402


class TestCLI(unittest.TestCase):
    @patch("sys.stdout", new_callable=StringIO)
    def test_help_without_command(self, stdout):
        code = cli.main([])

        self.assertEqual(code, 0)
        self.assertIn("install", stdout.getvalue())

    @patch("gemini_delegation.installer.install_hooks")
    def test_install_command_requires_target_and_forwards_it(self, mock_install):
        mock_install.return_value = 0

        code = cli.main(["install", "--target", "C:\\repo"])

        self.assertEqual(code, 0)
        mock_install.assert_called_once_with(target_dir="C:\\repo", create_target=False, preserve_claude_md=False)

    @patch("gemini_delegation.installer.verify_install")
    def test_verify_command_forwards_target(self, mock_verify):
        mock_verify.return_value = 0

        code = cli.main(["verify", "--target", "/tmp/repo"])

        self.assertEqual(code, 0)
        mock_verify.assert_called_once_with(target_dir="/tmp/repo")

    @patch("sys.stderr", new_callable=StringIO)
    def test_installer_error_is_actionable(self, stderr):
        from gemini_delegation.installer import InstallError

        with patch("gemini_delegation.installer.install_hooks", side_effect=InstallError("bad target")):
            code = cli.main(["install", "--target", "missing"])

        self.assertEqual(code, 2)
        self.assertIn("bad target", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
