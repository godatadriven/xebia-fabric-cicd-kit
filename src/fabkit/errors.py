"""Errors, check results, and the findings report."""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO


class FabkitError(Exception):
    """A failure the user must act on. Printed without a traceback."""

    def __init__(self, summary: str, details: list[str] | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.details = details or []


def display_path(path: Path) -> str:
    """The path relative to the current directory, or absolute when outside it."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Issue:
    """One problem: the file it is in, or None when it is in no single file."""

    file: Path | None
    message: str

    def __str__(self) -> str:
        return f"{display_path(self.file)}: {self.message}" if self.file else self.message


@dataclass
class Findings:
    """Errors fail a check; warnings are printed and do not."""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def extend(self, other: Findings) -> None:
        """Add another check's findings to these."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


# ANSI codes per label. Used only on a terminal, and not with NO_COLOR set.
COLORS = {"error": "31", "warning": "33", "file": "1"}


def _paint(text: str, kind: str, color: bool) -> str:
    return f"\033[{COLORS[kind]}m{text}\033[0m" if color else text


def _use_color(stream: TextIO) -> bool:
    return stream.isatty() and "NO_COLOR" not in os.environ


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def print_findings(findings: Findings, stream: TextIO | None = None) -> None:
    """Print the findings grouped by file, errors before warnings. Issues in no file come last."""
    stream = stream or sys.stderr
    color = _use_color(stream)
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for kind, issues in (("error", findings.errors), ("warning", findings.warnings)):
        for issue in dict.fromkeys(issues):
            groups[display_path(issue.file) if issue.file else ""].append((kind, issue.message))

    width = len("warning")
    for name in sorted(groups, key=lambda name: (name == "", name)):
        print(_paint(name or "Other", "file", color), file=stream)
        for kind, message in groups[name]:
            # Lines after the first line up under the message
            text = message.replace("\n", "\n" + " " * (width + 4))
            print(f"  {_paint(kind.ljust(width), kind, color)}  {text}", file=stream)
        print(file=stream)


def summarize_findings(findings: Findings) -> str:
    """'3 errors and 1 warning in 2 files'. A count of zero is left out."""
    files = {issue.file for issue in [*findings.errors, *findings.warnings] if issue.file}
    counts = " and ".join(
        _plural(len(set(issues)), word)
        for issues, word in ((findings.errors, "error"), (findings.warnings, "warning"))
        if issues
    )
    return f"{counts} in {_plural(len(files), 'file')}" if files else counts


def print_warnings(warnings: Sequence[str | Issue]) -> None:
    """Print warnings to stderr."""
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
