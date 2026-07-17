from __future__ import annotations

import argparse
import sys
import traceback

from . import installer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gemini-delegate",
        description="Compatibility interface for legacy per-repository installations.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("help")

    install = sub.add_parser("install")
    install.add_argument("--target", required=True)
    install.add_argument("--create-target", action="store_true")
    install.add_argument("--preserve-claude-md", action="store_true")
    install.add_argument("--no-update", action="store_true")

    verify = sub.add_parser("verify")
    verify.add_argument("--target", required=True)
    verify.add_argument("--preserve-claude-md", action="store_true")

    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--target", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {None, "help"}:
        parser.print_help()
        return 0
    try:
        if args.command == "install":
            return installer.install_hooks(
                target_dir=args.target,
                create_target=args.create_target,
                preserve_claude_md=args.preserve_claude_md,
                no_update=args.no_update,
            )
        if args.command == "verify":
            return installer.verify_install(
                target_dir=args.target,
                preserve_claude_md=args.preserve_claude_md,
            )
        return installer.uninstall_hooks(target_dir=args.target)
    except installer.InstallError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("[ERROR] Unexpected legacy installer failure.", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
