"""The deploy sequence: checks, deploy_with_config, uploads."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from fabkit.config import DeployConfig, check_config, load_deploy_config
from fabkit.connections import FABRIC_API_ROOT, FABRIC_SCOPE, get_json, verify_connections
from fabkit.errors import FabkitError, Findings
from fabkit.parameters import check_parameters
from fabkit.platform_files import check_logical_ids
from fabkit.takeover import take_over_semantic_models
from fabkit.uploads import Upload, check_uploads, onelake_client, run_uploads
from fabkit.value_sets import check_value_set_environments, check_value_sets
from fabkit.warnings import collect_fatal_logs


def _credential() -> Any:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def _deploy_with_config(**kwargs: Any) -> Any:
    from fabric_cicd import deploy_with_config

    return deploy_with_config(**kwargs)


def _change_log_level(level: str) -> None:
    from fabric_cicd import change_log_level

    change_log_level(level)


def resolve_workspace_id(credential: Any, config: DeployConfig, environment: str) -> str:
    """The workspace GUID: core.workspace_id, or a lookup of core.workspace by name."""
    workspace_id = config.value("core", "workspace_id", environment)
    if workspace_id:
        return str(workspace_id)
    name = config.value("core", "workspace", environment)
    token = credential.get_token(FABRIC_SCOPE).token
    url: str | None = f"{FABRIC_API_ROOT}/workspaces"
    while url:
        body = get_json(url, token)
        for workspace in body.get("value", []):
            if workspace.get("displayName") == name:
                return str(workspace["id"])
        url = body.get("continuationUri") or None
    raise FabkitError(
        f"Workspace {name!r} not found, or the deploy service principal has no access."
    )


def run_deploy(args: argparse.Namespace) -> int:
    """Check, deploy and upload for one environment."""
    config = load_deploy_config(Path(args.config))
    environment: str = args.environment
    if environment not in config.environments:
        raise FabkitError(
            f"Environment {environment!r} is not in {config.path.name}.",
            [f"Known environments: {', '.join(config.environments)}"],
        )

    findings = check_config(config)
    if not findings.errors:
        findings.extend(check_parameters(config, target_environment=environment))
    findings.extend(check_logical_ids(Path.cwd()))
    findings.extend(check_value_sets(Path.cwd()))
    findings.extend(check_value_set_environments(config))
    if findings.errors:
        raise FabkitError("Checks failed. Nothing was published.", findings.errors)
    _print_warnings(findings)

    uploads: list[Upload] = args.upload or []
    check_uploads(uploads)

    if os.environ.get("RUNNER_DEBUG") == "1":
        _change_log_level("DEBUG")

    credential = _credential()
    if args.skip_connection_check:
        print("Connection check skipped (--skip-connection-check).")
    else:
        verify_connections(credential, config, environment, args.connection_variables)

    if args.take_over_semantic_models:
        _print_warnings(
            take_over_semantic_models(
                credential,
                config,
                environment,
                lambda: resolve_workspace_id(credential, config, environment),
            )
        )

    with collect_fatal_logs() as collected:
        try:
            result = _deploy_with_config(
                config_file_path=str(config.path),
                token_credential=credential,
                environment=environment,
            )
        except Exception as error:  # fabric-cicd raises many types; all of them fail the deploy
            raise FabkitError(f"fabric-cicd deploy failed: {error}", collected.messages) from error
        _check_result(result)

        if uploads and not collected.messages:
            _run_uploads(credential, config, environment, uploads)

    if collected.messages:
        raise FabkitError("fabric-cicd logged problems that fail the deploy.", collected.messages)
    print(f"Deploy to {environment} completed.")
    return 0


def _print_warnings(findings: Findings) -> None:
    for warning in findings.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _check_result(result: Any) -> None:
    """Fail when fabric-cicd reports a status other than completed."""
    from fabric_cicd import DeploymentStatus

    status = getattr(result, "status", None)
    if status is not None and status != DeploymentStatus.COMPLETED:
        message = getattr(result, "message", "")
        raise FabkitError(f"fabric-cicd deploy ended with status {status}. {message}".strip())


def _run_uploads(
    credential: Any, config: DeployConfig, environment: str, uploads: list[Upload]
) -> None:
    """Resolve the workspace and copy the uploads into it."""
    try:
        workspace_id = resolve_workspace_id(credential, config, environment)
        run_uploads(onelake_client(credential), workspace_id, uploads)
    except FabkitError:
        raise
    except Exception as error:  # Azure SDK errors vary; all of them fail the upload
        raise FabkitError(f"Upload failed: {error}") from error
