"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from fabkit.errors import FabkitError, Findings
from fabkit.platform_files import check_logical_ids
from fabkit.value_sets import check_value_sets


def report(findings: Findings, what: str) -> int:
    """Print findings and return the exit code."""
    for warning in findings.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in findings.errors:
        print(f"error: {error}", file=sys.stderr)
    if findings.errors:
        print(f"{what} failed with {len(findings.errors)} error(s).", file=sys.stderr)
        return 1
    print(f"{what} passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The fabkit argument parser with every subcommand."""
    parser = argparse.ArgumentParser(prog="fabkit")
    parser.add_argument(
        "--version", action="version", version=f"fabkit {version('xebia-fabric-cicd-kit')}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    hook = subparsers.add_parser(
        "check-logical-ids", help="Check .platform logicalIds are unique (current directory)."
    )
    hook.set_defaults(func=lambda _: report(check_logical_ids(Path.cwd()), "logicalId check"))

    hook = subparsers.add_parser(
        "check-value-sets", help="Check VariableLibrary value-set overrides (current directory)."
    )
    hook.set_defaults(func=lambda _: report(check_value_sets(Path.cwd()), "Value-set check"))

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
