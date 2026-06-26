from __future__ import annotations

import argparse
import sys
import traceback


def install(args) -> int:
    from .installer import install_hooks

    return install_hooks(
        target_dir=args.target,
        create_target=args.create_target,
        preserve_claude_md=getattr(args, "preserve_claude_md", False),
    )


def uninstall(args) -> int:
    from .installer import uninstall_hooks

    return uninstall_hooks(target_dir=args.target)


def verify(args) -> int:
    from .installer import verify_install

    return verify_install(
        target_dir=args.target,
        preserve_claude_md=getattr(args, "preserve_claude_md", False),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gemini-delegate",
        description="Install local agy delegation hooks for Claude Code and Codex.",
    )
    subparsers = parser.add_subparsers(dest="command")
    help_parser = subparsers.add_parser("help", help="Show this help text")
    help_parser.set_defaults(handler=lambda args: parser.print_help() or 0)

    install_parser = subparsers.add_parser("install", help="Install local delegation files into a target repo")
    install_parser.add_argument("--target", required=True, help="Target repository directory")
    install_parser.add_argument("--create-target", action="store_true", help="Create the target directory if missing")
    install_parser.add_argument(
        "--preserve-claude-md",
        action="store_true",
        help=(
            "Skip CLAUDE.md migration. Use when the repo has a hand-authored CLAUDE.md "
            "that already imports @AGENTS.md on line 1."
        ),
    )
    install_parser.set_defaults(handler=install)

    verify_parser = subparsers.add_parser("verify", help="Verify an existing local delegation install")
    verify_parser.add_argument("--target", required=True, help="Target repository directory")
    verify_parser.add_argument(
        "--preserve-claude-md",
        action="store_true",
        help="Accept a multi-line CLAUDE.md that starts with @AGENTS.md (matches --preserve-claude-md install).",
    )
    verify_parser.set_defaults(handler=verify)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove managed delegation files from a target repo")
    uninstall_parser.add_argument("--target", required=True, help="Target repository directory")
    uninstall_parser.set_defaults(handler=uninstall)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None or args.command == "help":
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001 - CLI should convert all failures to actionable output.
        from .installer import InstallError

        if isinstance(exc, InstallError):
            print("[ERROR] " + str(exc), file=sys.stderr)
        else:
            print("[ERROR] Unexpected delegation installer failure.", file=sys.stderr)
            print("Paste the output below into an AI agent and ask it to fix the install.", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
