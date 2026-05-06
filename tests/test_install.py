"""
Unit tests for installer CLI selection.
Run with: python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from install import interactive_selection


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


if __name__ == "__main__":
    unittest.main()
