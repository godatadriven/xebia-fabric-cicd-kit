"""fabric-cicd warnings that fail a deploy."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager

# fabric-cicd logs these at WARNING and reports success.
# The item is left unbound, a variable library runs on the wrong value set,
# or an item removed from the repository stays in the workspace.
FATAL_WARNING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Connection ID .* not found for semantic model", re.IGNORECASE),
    re.compile(r"Unexpected connection_id type", re.IGNORECASE),
    re.compile(r"Skipping non-string connection_id list element", re.IGNORECASE),
    re.compile(r"Failed to bind semantic model", re.IGNORECASE),
    re.compile(r"Semantic model .* specified in parameter\.yml not found", re.IGNORECASE),
    re.compile(r"Invalid 'semantic_model_binding' type", re.IGNORECASE),
    re.compile(r"does not match any value sets", re.IGNORECASE),
    re.compile(r"settings\.json file not found for item", re.IGNORECASE),
    re.compile(r"^Failed to unpublish ", re.IGNORECASE),
)


class FatalLogCollector(logging.Handler):
    """Collects fabric-cicd ERROR records and the WARNING records in FATAL_WARNING_PATTERNS."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.levelno >= logging.ERROR:
            self.messages.append(message)
        elif any(pattern.search(message) for pattern in FATAL_WARNING_PATTERNS):
            self.messages.append(f"{message} [warning treated as failure]")


@contextmanager
def collect_fatal_logs() -> Iterator[FatalLogCollector]:
    """Attach a collector to the fabric_cicd logger for the duration of the block."""
    collector = FatalLogCollector()
    logger = logging.getLogger("fabric_cicd")
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)
