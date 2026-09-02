"""Command-line entry point. `screen <address>` is the Plan 1 deliverable;
`scan` runs the always-on volume-spike scanner (Plan 2)."""
from __future__ import annotations

import argparse
import re
import sys

from memescreen import orchestrator
from memescreen.render import render_report

_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memescreen")
    sub = parser.add_subparsers(dest="command")

    screen_p = sub.add_parser("screen", help="due-diligence report for one token")
    screen_p.add_argument("address", help="Solana token mint address")

    sub.add_parser("scan", help="continuous volume-spike scanner")
    sub.add_parser("journal", help="Discord trade-journal bot")
    return parser


def main(argv: list[str] | None = None) -> int:
    # stderr too: the invalid-address branch echoes the raw argument, which a
    # stock cp1252 Windows console cannot encode.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "screen":
        if not _ADDRESS_RE.match(args.address):
            print(f"error: '{args.address}' is not a valid Solana address", file=sys.stderr)
            return 2
        report = orchestrator.screen(args.address)
        print(render_report(report))
        return 0

    if args.command == "scan":
        from memescreen.scanner import run
        return run.main()

    if args.command == "journal":
        from memescreen.journal import bot
        return bot.run()

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
