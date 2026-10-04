"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version

from fabkit.errors import FabkitError


def build_parser() -> argparse.ArgumentParser:
    """The fabkit argument parser with every subcommand."""
    parser = argparse.ArgumentParser(prog="fabkit")
    parser.add_argument(
        "--version", action="version", version=f"fabkit {version('xebia-fabric-cicd-kit')}"
    )
    parser.add_subparsers(dest="command", required=True)
    return parser


def run(parser: argparse.ArgumentParser, argv: list[str] | None) -> int:
    """Parse argv and run the chosen subcommand."""
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FabkitError as error:
        print(error.summary, file=sys.stderr)
        for detail in error.details:
            print(f"- {detail}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    """Run fabkit."""
    return run(build_parser(), argv)
