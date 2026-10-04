import inspect
import logging

from fabric_cicd._items import _semanticmodel

from fabkit.warnings import FATAL_WARNING_PATTERNS, collect_fatal_logs

# The texts fabric-cicd 1.3.0 logs, with the f-string fields filled in
SAMPLES = (
    "Connection ID 'abc' not found for semantic model 'Sales'",
    "Unexpected connection_id type 'int'; expected str or list. Skipping.",
    "Skipping non-string connection_id list element: 3 (type 'int')",
)


# Each pattern matches the warning fabric-cicd logs.
def test_patterns_match_samples() -> None:
    for sample, pattern in zip(SAMPLES, FATAL_WARNING_PATTERNS, strict=True):
        assert pattern.search(sample), sample


# The pinned fabric-cicd still contains every warning text the patterns rely on.
def test_fabric_cicd_still_logs_these_texts() -> None:
    source = inspect.getsource(_semanticmodel)
    for fragment in (
        "not found for semantic model",
        "Unexpected connection_id type",
        "Skipping non-string connection_id list element",
    ):
        assert fragment in source, (
            f"fabric-cicd no longer logs {fragment!r}; update FATAL_WARNING_PATTERNS"
        )


# A matching WARNING and any ERROR are collected; other warnings are not.
def test_collector_keeps_errors_and_binding_warnings_only() -> None:
    logger = logging.getLogger("fabric_cicd.items")
    with collect_fatal_logs() as collected:
        logger.warning(SAMPLES[0])
        logger.warning("harmless")
        logger.error("publish failed")
    assert len(collected.messages) == 2


# The collector is removed after the block.
def test_collector_is_removed_after_block() -> None:
    with collect_fatal_logs() as collected:
        pass
    logging.getLogger("fabric_cicd").error("after")
    assert collected.messages == []
