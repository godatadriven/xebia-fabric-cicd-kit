import inspect
import logging

import pytest
from fabric_cicd import fabric_workspace
from fabric_cicd._items import _semanticmodel, _variablelibrary

from fabkit.warnings import FATAL_WARNING_PATTERNS, collect_check_logs, collect_fatal_logs

# The texts the pinned fabric-cicd logs, with the f-string fields filled in
SAMPLES = (
    "Connection ID 'abc' not found for semantic model 'Sales'",
    "Unexpected connection_id type 'int'; expected str or list. Skipping.",
    "Skipping non-string connection_id list element: 3 (type 'int')",
    "Failed to bind semantic model 'Sales' to connection 'abc'. Status code: 400",
    "Semantic model 'Sales' specified in parameter.yml not found in repository",
    "Invalid 'semantic_model_binding' type: str. Expected list or dict. Skipping semantic "
    "model binding.",
    "Provided target environment 'PROD' does not match any value sets.  Using 'Default value set'",
    "settings.json file not found for item vars. Active value set not changed.",
    "Failed to unpublish Notebook 'old'. Raw exception: 409 Conflict",
)


# Each pattern matches the warning fabric-cicd logs.
def test_patterns_match_samples() -> None:
    for sample, pattern in zip(SAMPLES, FATAL_WARNING_PATTERNS, strict=True):
        assert pattern.search(sample), sample


# The pinned fabric-cicd still contains every warning text the patterns rely on.
def test_fabric_cicd_still_logs_these_texts() -> None:
    source = "".join(
        inspect.getsource(module) for module in (_semanticmodel, _variablelibrary, fabric_workspace)
    )
    for fragment in (
        "not found for semantic model",
        "Unexpected connection_id type",
        "Skipping non-string connection_id list element",
        "Failed to bind semantic model",
        "specified in parameter.yml not found in repository",
        "Invalid 'semantic_model_binding' type",
        "does not match any value sets",
        "settings.json file not found for item",
        'f"Failed to unpublish {item_type}',
    ):
        assert fragment in source, (
            f"fabric-cicd no longer logs {fragment!r}; update FATAL_WARNING_PATTERNS"
        )


# Inside the block, ERRORs and matching WARNINGs are collected; after it, nothing is.
def test_collector_keeps_errors_and_fatal_warnings_only() -> None:
    logger = logging.getLogger("fabric_cicd.items")
    with collect_fatal_logs() as collected:
        logger.warning(SAMPLES[0])
        logger.warning("harmless")
        logger.error("publish failed")
    logger.error("after")

    assert collected.messages == [f"{SAMPLES[0]} [warning treated as failure]", "publish failed"]


# Inside the block nothing reaches the console; other warnings are kept, known noise is not.
def test_check_logs_are_collected_not_printed(capsys: pytest.CaptureFixture[str]) -> None:
    logger = logging.getLogger("fabric_cicd")
    console = logging.StreamHandler()
    logger.addHandler(console)
    try:
        with collect_check_logs() as collected:
            logger.error("validation failed")
            logger.warning("gateway is deprecated")
            logger.warning("Found the reserved environment key '_ALL_'")
            logger.warning("The replace value: 'x' will be applied for any target environment")
        assert console in logger.handlers
    finally:
        logger.removeHandler(console)

    assert capsys.readouterr().err == ""
    assert collected.messages == ["validation failed"]
    assert collected.warnings == ["gateway is deprecated"]
