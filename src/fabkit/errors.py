"""Errors and check results."""

from __future__ import annotations

from dataclasses import dataclass, field


class FabkitError(Exception):
    """A failure the user must act on. Printed without a traceback."""

    def __init__(self, summary: str, details: list[str] | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.details = details or []


@dataclass
class Findings:
    """Errors fail a check; warnings are printed and do not."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: Findings) -> None:
        """Add another check's findings to these."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
