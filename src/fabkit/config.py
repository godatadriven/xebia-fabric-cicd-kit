"""fabric-cicd config.yml reading and checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from fabkit.errors import FabkitError, Findings


@dataclass(frozen=True)
class DeployConfig:
    """The parts of a fabric-cicd config.yml that fabkit reads."""

    path: Path
    raw: dict[str, Any]

    @property
    def environments(self) -> list[str]:
        """Environment names: the keys of core.workspace or core.workspace_id."""
        core = self.raw.get("core") or {}
        for key in ("workspace", "workspace_id"):
            if isinstance(core.get(key), dict):
                return [str(name) for name in core[key]]
        raise FabkitError(
            f"{self.path.name}: core.workspace or core.workspace_id must map each "
            "environment to a workspace."
        )

    def value(self, section: str, key: str, environment: str) -> Any:
        """One setting for one environment. A plain value applies to every environment."""
        value = (self.raw.get(section) or {}).get(key)
        return value.get(environment) if isinstance(value, dict) else value

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


def load_deploy_config(path: Path) -> DeployConfig:
    """Read a fabric-cicd config.yml."""
    if not path.is_file():
        raise FabkitError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise FabkitError(f"{path.name}: expected a mapping at the top level.")
    return DeployConfig(path=path.resolve(), raw=raw)


def check_config(config: DeployConfig) -> Findings:
    """Check config.yml for every environment."""
    try:
        environments = config.environments
    except FabkitError as error:
        return Findings(errors=[error.summary])

    findings = Findings()
    findings.errors.extend(_fabric_cicd_errors(config, environments))
    findings.errors.extend(_unpublish_errors(config, environments))
    findings.errors.extend(_unreferenced_parameter_errors(config, environments))
    return findings


def _fabric_cicd_errors(config: DeployConfig, environments: list[str]) -> list[str]:
    """fabric-cicd's own config validation, once per environment."""
    try:
        from fabric_cicd._common._config_utils import load_config_file
    except ImportError as error:
        raise FabkitError(
            "fabric-cicd's config validator could not be imported; its private API moved.",
            [str(error)],
        ) from error

    errors = []
    for environment in environments:
        try:
            load_config_file(str(config.path), environment)
        except Exception as error:  # fabric-cicd raises ConfigValidationError with every message
            errors.append(f"fabric-cicd rejected {config.path.name} for {environment}: {error}")
    return errors


def _unpublish_errors(config: DeployConfig, environments: list[str]) -> list[str]:
    """unpublish.skip must be set for every environment."""
    unpublish = config.raw.get("unpublish")
    skip = unpublish.get("skip") if isinstance(unpublish, dict) else None
    if isinstance(skip, bool):
        return []
    if isinstance(skip, dict):
        missing = [name for name in environments if not isinstance(skip.get(name), bool)]
        if not missing:
            return []
        return [
            f"{config.path.name}: unpublish.skip is not set for {', '.join(missing)}. "
            "With false, workspace items that are not in the repository are removed."
        ]
    return [
        f"{config.path.name}: set unpublish.skip to true or false, for all or per environment. "
        "With false, workspace items that are not in the repository are removed."
    ]


def _unreferenced_parameter_errors(config: DeployConfig, environments: list[str]) -> list[str]:
    """A parameter.yml that config.yml does not reference is ignored by fabric-cicd."""
    errors = []
    for environment in environments:
        if config.parameter_file(environment) is not None:
            continue
        try:
            candidate = config.repository_directory(environment) / "parameter.yml"
        except FabkitError:
            continue
        if candidate.is_file():
            errors.append(
                f"{candidate} exists, but {config.path.name} has no core.parameter for "
                f"{environment}. fabric-cicd ignores the file."
            )
    return errors
