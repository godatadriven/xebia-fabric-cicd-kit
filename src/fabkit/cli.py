"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from fabkit.config import check_config, load_deploy_config
from fabkit.errors import FabkitError, Findings
from fabkit.parameters import check_parameters
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


def run_validate(args: argparse.Namespace) -> int:
    """File checks for every environment. Never logs in."""
    config = load_deploy_config(Path(args.config))
    findings = check_config(config)
    if not findings.errors:
        findings.extend(check_parameters(config, unmatched_find_value=args.unmatched_find_value))
    findings.extend(check_logical_ids(Path.cwd()))
    findings.extend(check_value_sets(Path.cwd()))
    return report(findings, "Validation")


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

    validate = subparsers.add_parser("validate", help="Check the repository files. No login.")
    validate.add_argument("--config", default="config.yml", help="fabric-cicd config file.")
    validate.add_argument(
        "--unmatched-find-value",
        choices=["error", "warning"],
        default="error",
        help="Whether a find_value that matches no file fails validation.",
    )
    validate.set_defaults(func=run_validate)

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
