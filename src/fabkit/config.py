"""fabric-cicd config.yml reading and checks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from fabkit.errors import FabkitError, Findings
from fabkit.platform_files import read_items

# Settings fabric-cicd reads per environment, besides features and constants
ENVIRONMENT_MAPPED_SETTINGS = {
    "core": ("item_types_in_scope",),
    "publish": (
        "exclude_regex",
        "folder_exclude_regex",
        "folder_path_to_include",
        "shortcut_exclude_regex",
        "items_to_include",
        "skip",
    ),
    "unpublish": ("exclude_regex", "items_to_include", "skip"),
}


@dataclass(frozen=True)
class DeployConfig:
    """The parts of a fabric-cicd config.yml that fabkit reads."""

    path: Path
    raw: dict[str, Any]

    def section(self, name: str) -> dict[str, Any]:
        """One top-level section of config.yml. A missing section is empty."""
        value = self.raw.get(name)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise FabkitError(f"{self.path.name}: {name} must be a mapping.")
        return value

    @property
    def environments(self) -> list[str]:
        """Environment names: the keys of core.workspace or core.workspace_id."""
        core = self.section("core")
        for key in ("workspace", "workspace_id"):
            if isinstance(core.get(key), dict):
                return [str(name) for name in core[key]]
        raise FabkitError(
            f"{self.path.name}: core.workspace or core.workspace_id must map each "
            "environment to a workspace."
        )

    def value(self, section: str, key: str, environment: str) -> Any:
        """One setting for one environment. A plain value applies to every environment."""
        value = self.section(section).get(key)
        return value.get(environment) if isinstance(value, dict) else value

    def features(self, environment: str) -> list[str]:
        """The feature flags for one environment."""
        value = self.raw.get("features")
        if isinstance(value, dict):
            value = value.get(environment)
        return [str(flag) for flag in value] if isinstance(value, list) else []

    def repository_directory(self, environment: str) -> Path:
        """The folder holding the Fabric items for one environment."""
        value = self.value("core", "repository_directory", environment)
        if not value:
            raise FabkitError(
                f"{self.path.name}: core.repository_directory is not set for {environment}."
            )
        return (self.path.parent / str(value)).resolve()

    def parameter_file(self, environment: str) -> Path | None:
        """The parameter file for one environment, or None when there is none."""
        value = self.value("core", "parameter", environment)
        return (self.path.parent / str(value)).resolve() if value else None

    def item_types(self, environment: str) -> list[str] | None:
        """The item types in scope for one environment. None means every type."""
        value = self.value("core", "item_types_in_scope", environment)
        return list(value) if value else None

    def in_scope(self, environment: str, item_type: str) -> bool:
        """Whether the environment deploys items of this type."""
        item_types = self.item_types(environment)
        return item_types is None or item_type in item_types


def require_environment(config: DeployConfig, environment: str) -> None:
    """Fail when config.yml has no workspace for the environment."""
    if environment not in config.environments:
        raise FabkitError(
            f"Environment {environment!r} is not in {config.path.name}.",
            [f"Known environments: {', '.join(config.environments)}"],
        )


def load_deploy_config(path: Path) -> DeployConfig:
    """Read a fabric-cicd config.yml."""
    if not path.is_file():
        raise FabkitError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise FabkitError(f"{path.name}: invalid YAML.", [str(error)]) from error
    if not isinstance(raw, dict):
        raise FabkitError(f"{path.name}: expected a mapping at the top level.")
    return DeployConfig(path=path.resolve(), raw=raw)


def check_config(config: DeployConfig) -> Findings:
    """Check config.yml for every environment."""
    try:
        environments = config.environments
    except FabkitError as error:
        return Findings(errors=[error.summary])

    directories = {}
    for environment in environments:
        try:
            directories[environment] = config.repository_directory(environment)
        except FabkitError:
            continue  # reported by fabric-cicd's own validation

    errors = [
        *_fabric_cicd_errors(config, environments),
        *_unpublish_errors(config),
        *_missing_environment_errors(config, environments),
        *_empty_repository_errors(config, directories),
        *_duplicate_item_errors(config, directories),
        *_unreferenced_parameter_errors(config, directories),
    ]
    return Findings(errors=list(dict.fromkeys(errors)))


def _fabric_cicd_errors(config: DeployConfig, environments: list[str]) -> list[str]:
    """fabric-cicd's own config validation, once per environment."""
    from fabric_cicd._common._config_utils import load_config_file

    errors = []
    for environment in environments:
        try:
            load_config_file(str(config.path), environment)
        except Exception as error:  # fabric-cicd raises ConfigValidationError with every message
            errors.append(f"fabric-cicd rejected {config.path.name} for {environment}: {error}")
    return errors


def _unpublish_errors(config: DeployConfig) -> list[str]:
    """unpublish.skip must be set, for all or per environment."""
    if isinstance(config.section("unpublish").get("skip"), bool | dict):
        return []
    return [
        f"{config.path.name}: set unpublish.skip to true or false, for all or per environment. "
        "With false, workspace items that are not in the repository are removed."
    ]


def _missing_environment_errors(config: DeployConfig, environments: list[str]) -> list[str]:
    """Per-environment settings that leave out an environment. fabric-cicd skips them there."""
    mappings = [
        (f"{section}.{key}", config.section(section).get(key))
        for section, keys in ENVIRONMENT_MAPPED_SETTINGS.items()
        for key in keys
    ]
    mappings.append(("features", config.raw.get("features")))
    mappings += [(f"constants.{key}", value) for key, value in config.section("constants").items()]

    errors = []
    for name, value in mappings:
        missing = [env for env in environments if isinstance(value, dict) and env not in value]
        if missing:
            errors.append(
                f"{config.path.name}: {name} is not set for {', '.join(missing)}. fabric-cicd "
                "ignores the setting for that environment; name every environment."
            )
    return errors


def _empty_repository_errors(config: DeployConfig, directories: dict[str, Path]) -> list[str]:
    """With unpublish on, a repository without items in scope empties the workspace."""
    errors = []
    for environment, directory in directories.items():
        # A missing folder is reported by fabric-cicd's own validation
        if config.value("unpublish", "skip", environment) is not False or not directory.is_dir():
            continue
        if any(config.in_scope(environment, item.type) for item in read_items(directory)):
            continue
        scope = config.item_types(environment)
        removed = f"{', '.join(scope)} item" if scope else "item"
        errors.append(
            f"{config.path.name}: {environment} deploys no items from {directory.name}/, and "
            f"unpublish.skip is false. The deploy would remove every {removed} from the "
            "workspace. Add items, or set unpublish.skip to true."
        )
    return errors


def _duplicate_item_errors(config: DeployConfig, directories: dict[str, Path]) -> list[str]:
    """Items in scope sharing a type and name. fabric-cicd deploys only one of them."""
    errors = []
    for environment, directory in directories.items():
        folders: dict[tuple[str, str], list[Path]] = defaultdict(list)
        for item in read_items(directory):
            if config.in_scope(environment, item.type):
                folders[(item.type, item.name)].append(item.folder)
        for (item_type, name), paths in folders.items():
            if len(paths) > 1:
                listed = ", ".join(str(path.relative_to(directory)) for path in paths)
                errors.append(
                    f"{item_type} {name!r} is in {listed}. fabric-cicd deploys only one of "
                    "them; rename one."
                )
    return errors


def _unreferenced_parameter_errors(config: DeployConfig, directories: dict[str, Path]) -> list[str]:
    """A parameter.yml that config.yml does not reference is ignored by fabric-cicd."""
    return [
        f"{directory / 'parameter.yml'} exists, but {config.path.name} has no core.parameter "
        f"for {environment}. fabric-cicd ignores the file."
        for environment, directory in directories.items()
        if config.parameter_file(environment) is None and (directory / "parameter.yml").is_file()
    ]
