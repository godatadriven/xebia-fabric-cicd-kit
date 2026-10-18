"""parameter.yml checks."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, NamedTuple

import yaml

from fabkit.config import DeployConfig
from fabkit.errors import FabkitError, Findings
from fabkit.platform_files import item_names
from fabkit.warnings import collect_fatal_logs

# fabric-cicd's key for "same value in every environment"
ALL_ENVIRONMENTS_KEY = "_ALL_"

# Marks a value that cannot exist yet. Allowed everywhere except the deploy target.
PENDING_MARKER = "PLACEHOLDER"

# Leftovers from filling in a file: the words TODO and FIXME, <angle-bracketed>, XXXXXXXX
PLACEHOLDER_PATTERN = re.compile(r"\bTODO\b|\bFIXME\b|^<.*>$|XXXXXXXX", re.IGNORECASE)

# fabric-cicd resolves values starting with this at deploy, e.g. "$workspace.$id"
DYNAMIC_VALUE_PREFIX = "$"

# fabric-cicd's environment variable token: "$ENV:LAKEHOUSE_ID" -> LAKEHOUSE_ID
ENV_REFERENCE_PATTERN = re.compile(r"\$ENV:(\w+)")

# Value given to an unset $ENV: variable while validating another environment
ENV_STAND_IN = "unset"

GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
FABRIC_HOSTNAME_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9.-]*\.fabric\.microsoft\.com$", re.IGNORECASE
)

# The first shape the find_value matches is the shape every replace_value must have
VALUE_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GUID", GUID_PATTERN),
    ("Fabric hostname", FABRIC_HOSTNAME_PATTERN),
)


class EnvironmentBlock(NamedTuple):
    """One environment-keyed mapping from the parameter file."""

    description: str
    values: dict[str, Any]
    find_value: str | None


def check_parameters(
    config: DeployConfig,
    *,
    unmatched_find_value: Literal["error", "warning"] = "error",
    target_environment: str | None = None,
) -> Findings:
    """Check every parameter file config.yml references.

    With a target_environment, a pending value that would reach it is an error.
    """
    findings = Findings()
    for parameter_file, environments in _files_by_environment(config).items():
        parameters = _load(parameter_file)
        repository_directory = config.repository_directory(environments[0])
        blocks = iter_environment_blocks(parameters)

        findings.errors.extend(
            _fabric_cicd_errors(
                config, parameter_file, repository_directory, environments, target_environment
            )
        )
        if any(_semantic_models_in_scope(config, name) for name in environments):
            findings.errors.extend(_unknown_model_errors(parameters, repository_directory))
        for block in blocks:
            findings.errors.extend(_environment_key_errors(block, environments))
            findings.errors.extend(_placeholder_errors(block))
            findings.errors.extend(_shape_errors(block))
            findings.errors.extend(_identical_value_errors(block))
            pending = _pending_messages(block, target_environment)
            if target_environment is None:
                findings.warnings.extend(pending)
            else:
                findings.errors.extend(pending)

        unmatched = _unmatched_find_values(
            parameters, repository_directory, set(_files_by_environment(config))
        )
        if unmatched_find_value == "error":
            findings.errors.extend(unmatched)
        else:
            findings.warnings.extend(unmatched)
    return findings


def _semantic_models_in_scope(config: DeployConfig, environment: str) -> bool:
    item_types = config.item_types(environment)
    return item_types is None or "SemanticModel" in item_types


def _unknown_model_errors(parameters: dict[str, Any], repository_directory: Path) -> list[str]:
    """Bindings to semantic models that are not in the repository. fabric-cicd skips them."""
    binding = parameters.get("semantic_model_binding")
    if isinstance(binding, dict):
        entries = [
            (f"semantic_model_binding.models[{i}]", e)
            for i, e in enumerate_mappings(binding.get("models"))
        ]
    else:
        entries = [(f"semantic_model_binding[{i}]", e) for i, e in enumerate_mappings(binding)]
    known = item_names(repository_directory, "SemanticModel")
    errors = []
    for where, entry in entries:
        names = entry.get("semantic_model_name")
        for name in names if isinstance(names, list) else [names]:
            if isinstance(name, str) and name not in known:
                errors.append(
                    f"{where}: semantic model {name!r} is not in the repository under "
                    f"{repository_directory.name}/. The binding is skipped."
                )
    return errors


def _files_by_environment(config: DeployConfig) -> dict[Path, list[str]]:
    """Group environments by the parameter file they use."""
    files: dict[Path, list[str]] = {}
    for environment in config.environments:
        parameter_file = config.parameter_file(environment)
        if parameter_file is not None:
            files.setdefault(parameter_file, []).append(environment)
    return files


def _load(parameter_file: Path) -> dict[str, Any]:
    """Read the parameter file as a mapping."""
    if not parameter_file.is_file():
        raise FabkitError(f"Parameter file not found: {parameter_file}")
    try:
        loaded = yaml.safe_load(parameter_file.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise FabkitError(f"{parameter_file.name}: invalid YAML.", [str(error)]) from error
    if not isinstance(loaded, dict):
        raise FabkitError(f"{parameter_file.name}: expected a mapping at the top level.")
    return loaded


def enumerate_mappings(value: Any) -> list[tuple[int, dict[str, Any]]]:
    """The mapping entries of a list, with their 1-based position.

    Anything else is left for fabric-cicd's validator to report.
    """
    if not isinstance(value, list):
        return []
    return [(index, entry) for index, entry in enumerate(value, start=1) if isinstance(entry, dict)]


def iter_environment_blocks(parameters: dict[str, Any]) -> list[EnvironmentBlock]:
    """Every environment-keyed mapping in the parameter file."""
    blocks: list[EnvironmentBlock] = []
    for index, entry in enumerate_mappings(parameters.get("find_replace")):
        replace_value = entry.get("replace_value")
        if isinstance(replace_value, dict):
            find_value = entry.get("find_value")
            blocks.append(
                EnvironmentBlock(
                    f"find_replace[{index}] (find_value: {find_value!r})",
                    replace_value,
                    find_value if isinstance(find_value, str) else None,
                )
            )
    for name, key in (("key_value_replace", "find_key"), ("spark_pool", "instance_pool_id")):
        for index, entry in enumerate_mappings(parameters.get(name)):
            replace_value = entry.get("replace_value")
            if isinstance(replace_value, dict):
                blocks.append(
                    EnvironmentBlock(
                        f"{name}[{index}] ({key}: {entry.get(key)!r})", replace_value, None
                    )
                )

    binding = parameters.get("semantic_model_binding")
    if isinstance(binding, list):
        for index, entry in enumerate_mappings(binding):
            if isinstance(entry.get("connection_id"), dict):
                name = entry.get("semantic_model_name", "<unnamed>")
                blocks.append(
                    EnvironmentBlock(
                        f"semantic_model_binding[{index}] ({name})", entry["connection_id"], None
                    )
                )
        return blocks
    if not isinstance(binding, dict):
        return blocks
    default = binding.get("default")
    if isinstance(default, dict) and isinstance(default.get("connection_id"), dict):
        blocks.append(
            EnvironmentBlock(
                "semantic_model_binding.default.connection_id", default["connection_id"], None
            )
        )
    for index, model in enumerate_mappings(binding.get("models")):
        if isinstance(model.get("connection_id"), dict):
            name = model.get("semantic_model_name", "<unnamed>")
            blocks.append(
                EnvironmentBlock(
                    f"semantic_model_binding.models[{index}] ({name})", model["connection_id"], None
                )
            )
    return blocks


def _values_of(block: EnvironmentBlock, key: str) -> list[str]:
    """One key's values as strings; a single value becomes a list of one."""
    value = block.values[key]
    return [str(item) for item in (value if isinstance(value, list) else [value])]


def _is_pending(text: str) -> bool:
    """Whether the value carries the pending marker."""
    return PENDING_MARKER in text


def _environment_key_errors(block: EnvironmentBlock, environments: list[str]) -> list[str]:
    """Every environment named exactly once, or _ALL_ alone."""
    keys = {str(key) for key in block.values}
    if ALL_ENVIRONMENTS_KEY in {key.upper() for key in keys}:
        if len(keys) != 1:
            return [
                f"{block.description}: {ALL_ENVIRONMENTS_KEY!r} must be the only key, "
                f"found {sorted(keys)}."
            ]
        return []

    expected = set(environments)
    missing = expected - keys
    unexpected = keys - expected
    expected_by_upper = {name.upper(): name for name in expected}
    miscased = {key for key in unexpected if key.upper() in expected_by_upper}

    errors = []
    if miscased:
        right = sorted(expected_by_upper[key.upper()] for key in miscased)
        errors.append(
            f"{block.description}: wrong casing on {sorted(miscased)}. Keys are matched "
            f"exactly; use {right}."
        )
    for key in sorted(unexpected - miscased):
        errors.append(f"{block.description}: unknown environment key {key!r}.")
    # A miscased key is already reported; do not report its environment as missing too
    missing -= {expected_by_upper[key.upper()] for key in miscased}
    if missing:
        errors.append(
            f"{block.description}: environment key(s) {sorted(missing)} missing. "
            "Name every environment, even when the value is the same."
        )
    return errors


def _placeholder_errors(block: EnvironmentBlock) -> list[str]:
    """Values that still look like a placeholder."""
    return [
        f"{block.description}[{key}]: placeholder {text!r} was never filled in."
        for key in block.values
        for text in _values_of(block, key)
        if not _is_pending(text) and PLACEHOLDER_PATTERN.search(text)
    ]


def _shape_errors(block: EnvironmentBlock) -> list[str]:
    """Values shaped unlike their find_value, e.g. a GUID with a character missing."""
    if not block.find_value:
        return []
    shape = next(((n, p) for n, p in VALUE_SHAPES if p.match(block.find_value)), None)
    if shape is None:
        return []
    shape_name, pattern = shape
    return [
        f"{block.description}[{key}]: {text!r} does not have the shape of a {shape_name}, "
        "while find_value does."
        for key, text in block.values.items()
        if isinstance(text, str)
        and not text.startswith(DYNAMIC_VALUE_PREFIX)
        and not _is_pending(text)
        and not PLACEHOLDER_PATTERN.search(text)
        and not pattern.match(text)
    ]


def _identical_value_errors(block: EnvironmentBlock) -> list[str]:
    """Every environment with the same value: either _ALL_ was meant, or one is wrong."""
    if len(block.values) < 2:
        return []
    per_key = [_values_of(block, key) for key in block.values]
    if any(_is_pending(t) or PLACEHOLDER_PATTERN.search(t) for values in per_key for t in values):
        return []
    if len({tuple(values) for values in per_key}) != 1:
        return []
    return [
        f"{block.description}: every environment has the same value {per_key[0]!r}. "
        f"Use {ALL_ENVIRONMENTS_KEY!r} if that is intended; otherwise one environment "
        "points at the wrong source."
    ]


def _pending_messages(block: EnvironmentBlock, target_environment: str | None) -> list[str]:
    """Pending values: all of them, or only those the target environment would receive."""
    messages = []
    for key in block.values:
        applies = (
            target_environment is None
            or str(key) == target_environment
            or str(key).upper() == ALL_ENVIRONMENTS_KEY
        )
        if applies and any(_is_pending(text) for text in _values_of(block, key)):
            where = f" and would be deployed to {target_environment}" if target_environment else ""
            messages.append(f"{block.description}[{key}]: still {PENDING_MARKER}{where}.")
    return messages


def _unmatched_find_values(
    parameters: dict[str, Any], repository_directory: Path, parameter_files: set[Path]
) -> list[str]:
    """find_values that occur in no repository file. fabric-cicd skips them silently."""
    entries = enumerate_mappings(parameters.get("find_replace"))
    if not entries:
        return []

    contents = []
    for path in repository_directory.rglob("*"):
        # Every find_value occurs in the parameter file itself
        if not path.is_file() or path.resolve() in parameter_files:
            continue
        try:
            contents.append(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            continue
    lowered = [text.lower() for text in contents]

    messages = []
    for index, entry in entries:
        find_value = str(entry.get("find_value") or "")
        if (
            not find_value
            or find_value.startswith(DYNAMIC_VALUE_PREFIX)
            or _is_true(entry.get("is_regex"))
        ):
            continue
        if _is_true(entry.get("ignore_case")):
            found = any(find_value.lower() in text for text in lowered)
        else:
            found = any(find_value in text for text in contents)
        if not found:
            messages.append(
                f"find_replace[{index}]: find_value {find_value!r} matches nothing under "
                f"{repository_directory.name}/. The replacement is silently skipped."
            )
    return messages


def _is_true(flag: Any) -> bool:
    """fabric-cicd's reading of a "true"/"false" option."""
    return str(flag).lower() == "true"


@contextmanager
def _stand_in_env_variables(names: set[str]) -> Iterator[None]:
    """Set each unset variable to ENV_STAND_IN for the duration of the block."""
    missing = sorted(name for name in names if name not in os.environ)
    try:
        for name in missing:
            os.environ[name] = ENV_STAND_IN
        yield
    finally:
        for name in missing:
            os.environ.pop(name, None)


def _fabric_cicd_errors(
    config: DeployConfig,
    parameter_file: Path,
    repository_directory: Path,
    environments: list[str],
    target_environment: str | None,
) -> list[str]:
    """fabric-cicd's own parameter validation, once per environment. Uses a private API.

    config.yml's feature flags apply, as in deploy_with_config. $ENV: variables must be set
    only for the deploy target.
    """
    try:
        from fabric_cicd._common._config_utils import config_overrides_scope
        from fabric_cicd._parameter._parameter import Parameter
        from fabric_cicd.constants import ACCEPTED_ITEM_TYPES
    except ImportError as error:
        raise FabkitError(
            "fabric-cicd's parameter validator could not be imported; its private API moved.",
            [str(error)],
        ) from error

    env_references = set(ENV_REFERENCE_PATTERN.findall(parameter_file.read_text(encoding="utf-8")))
    errors = []
    for environment in environments:
        stand_ins = set() if environment == target_environment else env_references
        # fabric-cicd logs some errors and still passes the file; deploy fails on them
        with (
            config_overrides_scope(config.raw, environment),
            _stand_in_env_variables(stand_ins),
            collect_fatal_logs() as collected,
        ):
            parameter = Parameter(
                repository_directory=repository_directory,
                item_type_in_scope=config.item_types(environment) or list(ACCEPTED_ITEM_TYPES),
                environment=environment,
                parameter_file_path=str(parameter_file),
            )
            try:
                valid = parameter._validate_parameter_file()
            except Exception as error:  # fabric-cicd's validator crashes on some malformed entries
                errors.append(
                    f"fabric-cicd could not validate {parameter_file.name} for {environment}: "
                    f"{type(error).__name__}: {error}. Check the structure of every entry."
                )
                continue
        errors.extend(
            f"fabric-cicd, {parameter_file.name} for {environment}: {message}"
            for message in collected.messages
        )
        if not valid:
            errors.append(f"fabric-cicd rejected {parameter_file.name} for {environment}.")
    return errors
