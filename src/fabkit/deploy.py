"""The deploy sequence: checks, deploy_with_config, uploads."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from fabkit import workspaces
from fabkit.config import DeployConfig, check_config, load_deploy_config, require_environment
from fabkit.connections import verify_connections
from fabkit.errors import FabkitError, Findings
from fabkit.parameters import check_parameters
from fabkit.platform_files import check_logical_ids
from fabkit.takeover import take_over_semantic_models
from fabkit.uploads import Upload, check_uploads, upload_all, upload_warnings
from fabkit.value_sets import check_value_set_environments, check_value_sets
from fabkit.warnings import collect_fatal_logs


def _deploy_with_config(**kwargs: Any) -> Any:
    from fabric_cicd import deploy_with_config

    return deploy_with_config(**kwargs)


def _change_log_level(level: str) -> None:
    from fabric_cicd import change_log_level

    change_log_level(level)


def run_deploy(args: argparse.Namespace) -> int:
    """Check, deploy and upload for one environment."""
    config = load_deploy_config(Path(args.config))
    environment: str = args.environment
    require_environment(config, environment)

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
    for upload in uploads:
        _print_warnings(Findings(warnings=upload_warnings(upload)))

    if os.environ.get("RUNNER_DEBUG") == "1":
        _change_log_level("DEBUG")

    credential = workspaces.default_credential()
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
                lambda: workspaces.resolve_workspace_id(credential, config, environment),
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
            _run_uploads(credential, config, environment, uploads, args.upload_replace_existing)

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
    credential: Any,
    config: DeployConfig,
    environment: str,
    uploads: list[Upload],
    replace_existing: bool,
) -> None:
    """Resolve the workspace and replace each upload's folder in it."""
    workspace_id = workspaces.resolve_workspace_id(credential, config, environment)
    upload_all(credential, workspace_id, uploads, replace_existing)
