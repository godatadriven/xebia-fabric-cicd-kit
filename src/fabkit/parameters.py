"""parameter.yml checks."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, NamedTuple

import yaml

from fabkit.config import DeployConfig
from fabkit.errors import FabkitError, Findings
from fabkit.platform_files import read_items
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

# Value given to an unset $ENV: variable while validating another environment.
# fabric-cicd requires a GUID for a connection_id.
ENV_STAND_IN = "00000000-0000-0000-0000-000000000000"

# The feature flag that turns on $ENV: values
ENV_REPLACEMENT_FLAG = "enable_environment_variable_replacement"

GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# The first shape the find_value matches is the shape every replace_value must have
VALUE_SHAPES = (
    ("GUID", GUID_PATTERN),
    ("Fabric hostname", re.compile(r"^[a-z0-9][a-z0-9.-]*\.fabric\.microsoft\.com$", re.I)),
)

# Parameters with an environment-keyed replace_value, and the key that names an entry
REPLACE_PARAMETERS = (
    ("find_replace", "find_value"),
    ("key_value_replace", "find_key"),
    ("spark_pool", "instance_pool_id"),
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
        parameters = load_parameters(parameter_file)
        errors = _fabric_cicd_errors(
            config, parameter_file, parameters, environments, target_environment
        )
        errors += [
            error
            for environment in environments
            if config.in_scope(environment, "SemanticModel")
            for error in _unknown_model_errors(parameters, config.repository_directory(environment))
        ]
        pending = []
        for block in environment_blocks(parameters):
            errors += _environment_key_errors(block, environments)
            errors += _placeholder_errors(block)
            errors += _shape_errors(block)
            errors += _identical_value_errors(block)
            pending += _pending_messages(block, target_environment)
        unmatched = _unmatched_find_values(config, parameters, environments)

        findings.errors += list(dict.fromkeys(errors))
        pending_to = findings.warnings if target_environment is None else findings.errors
        pending_to.extend(pending)
        unmatched_to = findings.errors if unmatched_find_value == "error" else findings.warnings
        unmatched_to.extend(unmatched)
    return findings


def _files_by_environment(config: DeployConfig) -> dict[Path, list[str]]:
    """Group environments by the parameter file they use."""
    files: dict[Path, list[str]] = {}
    for environment in config.environments:
        parameter_file = config.parameter_file(environment)
        if parameter_file is not None:
            files.setdefault(parameter_file, []).append(environment)
    return files


def load_parameters(parameter_file: Path) -> dict[str, Any]:
    """The parameter file as a mapping, with the templates under extend merged in.

    $ENV: values stay as written. fabric-cicd's validation reports a template it cannot read.
    """
    try:
        loaded = yaml.safe_load(parameter_file.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise FabkitError(f"{parameter_file.name}: invalid YAML.", [str(error)]) from error
    if not isinstance(loaded, dict):
        raise FabkitError(f"{parameter_file.name}: expected a mapping at the top level.")
    if "extend" not in loaded:
        return loaded

    from fabric_cicd._parameter._parameter import Parameter

    parameter = Parameter(
        repository_directory=parameter_file.parent,
        item_type_in_scope=[],
        environment="",
        parameter_file_path=str(parameter_file),
    )
    return dict(parameter.environment_parameter)


def enumerate_mappings(value: Any) -> list[tuple[int, dict[str, Any]]]:
    """The mapping entries of a list, with their 1-based position.

    Anything else is left for fabric-cicd's validator to report.
    """
    if not isinstance(value, list):
        return []
    return [(index, entry) for index, entry in enumerate(value, start=1) if isinstance(entry, dict)]


def binding_entries(parameters: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The semantic_model_binding entries, legacy list or current format, each described."""
    binding = parameters.get("semantic_model_binding")
    prefix, models = "semantic_model_binding", binding
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(binding, dict):
        prefix, models = "semantic_model_binding.models", binding.get("models")
        if isinstance(binding.get("default"), dict):
            entries.append(("semantic_model_binding.default", binding["default"]))
    for index, entry in enumerate_mappings(models):
        name = entry.get("semantic_model_name", "<unnamed>")
        entries.append((f"{prefix}[{index}] ({name})", entry))
    return entries


def environment_blocks(parameters: dict[str, Any]) -> list[EnvironmentBlock]:
    """Every environment-keyed mapping in the parameter file."""
    blocks = []
    for parameter, key in REPLACE_PARAMETERS:
        for index, entry in enumerate_mappings(parameters.get(parameter)):
            find_value = entry.get("find_value")
            if isinstance(entry.get("replace_value"), dict):
                blocks.append(
                    EnvironmentBlock(
                        f"{parameter}[{index}] ({key}: {entry.get(key)!r})",
                        entry["replace_value"],
                        find_value if isinstance(find_value, str) else None,
                    )
                )
    for description, entry in binding_entries(parameters):
        if isinstance(entry.get("connection_id"), dict):
            blocks.append(EnvironmentBlock(description, entry["connection_id"], None))
    return blocks


def environment_value(values: dict[str, Any], environment: str) -> Any:
    """The value for one environment: its own key, or _ALL_ in any casing."""
    if environment in values:
        return values[environment]
    return next((v for k, v in values.items() if str(k).upper() == ALL_ENVIRONMENTS_KEY), None)


def resolve_env_references(config: DeployConfig, environment: str, value: str) -> str:
    """The value with each $ENV: reference replaced by its variable, as fabric-cicd's deploy does.

    "$ENV:SM_CONNECTION" -> the value of SM_CONNECTION. Without the feature flag, or with the
    variable unset, the reference stays.
    """
    if ENV_REPLACEMENT_FLAG not in config.features(environment):
        return value
    return ENV_REFERENCE_PATTERN.sub(lambda match: os.environ.get(match[1], match[0]), value)


def _unknown_model_errors(parameters: dict[str, Any], repository_directory: Path) -> list[str]:
    """Bindings to semantic models that are not in the repository. fabric-cicd skips them."""
    known = {item.name for item in read_items(repository_directory) if item.type == "SemanticModel"}
    errors = []
    for description, entry in binding_entries(parameters):
        names = entry.get("semantic_model_name")
        for name in names if isinstance(names, list) else [names]:
            if isinstance(name, str) and name not in known:
                errors.append(
                    f"{description}: semantic model {name!r} is not in the repository under "
                    f"{repository_directory.name}/. The binding is skipped."
                )
    return errors


def _values_of(block: EnvironmentBlock, key: str) -> list[str]:
    """One key's values as strings; a single value becomes a list of one."""
    value = block.values[key]
    return [str(item) for item in (value if isinstance(value, list) else [value])]


def _is_unfilled(text: str) -> bool:
    """Whether the value is pending or still a placeholder."""
    return PENDING_MARKER in text or bool(PLACEHOLDER_PATTERN.search(text))


def _environment_key_errors(block: EnvironmentBlock, environments: list[str]) -> list[str]:
    """Every environment named exactly once. fabric-cicd checks that _ALL_ stands alone."""
    keys = {str(key) for key in block.values}
    if ALL_ENVIRONMENTS_KEY in {key.upper() for key in keys}:
        return []

    expected_by_upper = {name.upper(): name for name in environments}
    unexpected = keys - set(environments)
    miscased = {key for key in unexpected if key.upper() in expected_by_upper}
    # A miscased key is reported once, not also as missing
    missing = set(environments) - keys - {expected_by_upper[key.upper()] for key in miscased}

    errors = []
    if miscased:
        right = sorted(expected_by_upper[key.upper()] for key in miscased)
        errors.append(
            f"{block.description}: wrong casing on {sorted(miscased)}. Keys are matched "
            f"exactly; use {right}."
        )
    errors += [
        f"{block.description}: unknown environment key {key!r}."
        for key in sorted(unexpected - miscased)
    ]
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
        if PENDING_MARKER not in text and PLACEHOLDER_PATTERN.search(text)
    ]


def _shape_errors(block: EnvironmentBlock) -> list[str]:
    """Values shaped unlike their find_value, e.g. a GUID with a character missing."""
    shape = next(
        ((name, p) for name, p in VALUE_SHAPES if block.find_value and p.match(block.find_value)),
        None,
    )
    if shape is None:
        return []
    shape_name, pattern = shape
    return [
        f"{block.description}[{key}]: {text!r} does not have the shape of a {shape_name}, "
        "while find_value does."
        for key, text in block.values.items()
        if isinstance(text, str)
        and not text.startswith(DYNAMIC_VALUE_PREFIX)
        and not _is_unfilled(text)
        and not pattern.match(text)
    ]


def _identical_value_errors(block: EnvironmentBlock) -> list[str]:
    """Every environment with the same value: either _ALL_ was meant, or one is wrong."""
    per_key = [_values_of(block, key) for key in block.values]
    if len(per_key) < 2 or any(_is_unfilled(text) for values in per_key for text in values):
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
    where = f" and would be deployed to {target_environment}" if target_environment else ""
    return [
        f"{block.description}[{key}]: still {PENDING_MARKER}{where}."
        for key in block.values
        if target_environment in (None, str(key)) or str(key).upper() == ALL_ENVIRONMENTS_KEY
        if any(PENDING_MARKER in text for text in _values_of(block, key))
    ]


class ItemFile(NamedTuple):
    """One text file of a Fabric item, as fabric-cicd's find_replace sees it."""

    item_type: str
    item_name: str
    path: Path
    text: str


def _item_files(config: DeployConfig, environment: str) -> list[ItemFile]:
    """The text files of every item in scope. fabric-cicd replaces values only in these."""
    files = []
    for item in read_items(config.repository_directory(environment)):
        if not config.in_scope(environment, item.type):
            continue
        for path in sorted(item.folder.rglob("*")):
            try:
                text = path.read_text(encoding="utf-8") if path.is_file() else None
            except (UnicodeDecodeError, OSError):
                continue
            if text is not None:
                files.append(ItemFile(item.type, item.name, path, text))
    return files


def _unmatched_find_values(
    config: DeployConfig, parameters: dict[str, Any], environments: list[str]
) -> list[str]:
    """find_values that occur in no file their filters select. fabric-cicd skips them silently."""
    from fabric_cicd._parameter._utils import check_replacement, process_input_path

    entries = [
        (index, entry, str(entry.get("find_value") or ""))
        for index, entry in enumerate_mappings(parameters.get("find_replace"))
    ]
    entries = [
        (index, entry, find_value)
        for index, entry, find_value in entries
        if find_value
        and not find_value.startswith(DYNAMIC_VALUE_PREFIX)
        and not _is_true(entry.get("is_regex"))
    ]
    unmatched: dict[int, list[str]] = {}
    for environment in environments if entries else []:
        directory = config.repository_directory(environment)
        files = _item_files(config, environment)
        for index, entry, find_value in entries:
            ignore_case = _is_true(entry.get("ignore_case"))
            paths = process_input_path(directory, entry.get("file_path"))
            needle = find_value.lower() if ignore_case else find_value
            if not any(
                needle in (file.text.lower() if ignore_case else file.text)
                for file in files
                if check_replacement(
                    entry.get("item_type"),
                    entry.get("item_name"),
                    paths,
                    file.item_type,
                    file.item_name,
                    file.path,
                )
            ):
                unmatched.setdefault(index, []).append(environment)

    return [
        f"find_replace[{index}]: find_value {find_value!r} matches nothing in the item files it "
        f"applies to for {', '.join(unmatched[index])}. The replacement is silently skipped."
        for index, _, find_value in entries
        if index in unmatched
    ]


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
    parameters: dict[str, Any],
    environments: list[str],
    target_environment: str | None,
) -> list[str]:
    """fabric-cicd's own parameter validation, once per environment that uses the file.

    config.yml's feature flags apply, as in deploy_with_config. $ENV: variables must be set
    only for the deploy target.
    """
    from fabric_cicd._common._config_utils import config_overrides_scope
    from fabric_cicd._parameter._parameter import Parameter
    from fabric_cicd.constants import ACCEPTED_ITEM_TYPES

    env_references = set(ENV_REFERENCE_PATTERN.findall(json.dumps(parameters)))
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
                repository_directory=config.repository_directory(environment),
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
        errors += [
            f"fabric-cicd, {parameter_file.name} for {environment}: {message}"
            for message in collected.messages
        ]
        if not valid:
            errors.append(f"fabric-cicd rejected {parameter_file.name} for {environment}.")
    return errors
