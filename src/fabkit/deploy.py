"""The file checks, and the deploy sequence: checks, deploy_with_config, uploads."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Literal

from fabkit import workspaces
from fabkit.config import DeployConfig, check_config, load_deploy_config, require_environment
from fabkit.connections import verify_connections
from fabkit.errors import FabkitError, Findings, print_warnings
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


def run_checks(
    config: DeployConfig,
    target_environment: str | None = None,
    unmatched_find_value: Literal["error", "warning"] = "error",
) -> Findings:
    """Every file check. With a target_environment, as for a deploy to it."""
    findings = check_config(config)
    if not findings.errors:
        findings.extend(
            check_parameters(
                config,
                target_environment=target_environment,
                unmatched_find_value=unmatched_find_value,
            )
        )
    findings.extend(check_logical_ids(Path.cwd()))
    findings.extend(check_value_sets(Path.cwd()))
    findings.extend(check_value_set_environments(config))
    return findings


def run_deploy(args: argparse.Namespace) -> int:
    """Check, deploy and upload for one environment."""
    config = load_deploy_config(Path(args.config))
    environment: str = args.environment
    require_environment(config, environment)

    findings = run_checks(config, target_environment=environment)
    if findings.errors:
        raise FabkitError("Checks failed. Nothing was published.", findings.errors)
    print_warnings(findings.warnings)

    uploads: list[Upload] = args.upload or []
    check_uploads(uploads)
    for upload in uploads:
        print_warnings(upload_warnings(upload))

    if os.environ.get("RUNNER_DEBUG") == "1":
        _change_log_level("DEBUG")

    credential = workspaces.default_credential()
    if args.skip_connection_check:
        print("Connection check skipped (--skip-connection-check).")
    else:
        verify_connections(credential, config, environment, args.connection_variables)

    if args.take_over_semantic_models:
        takeover = take_over_semantic_models(
            credential,
            config,
            environment,
            lambda: workspaces.resolve_workspace_id(credential, config, environment),
        )
        print_warnings(takeover.warnings)

    with collect_fatal_logs() as collected:
        try:
            _deploy_with_config(
                config_file_path=str(config.path),
                token_credential=credential,
                environment=environment,
            )
        except Exception as error:  # fabric-cicd raises many types; all of them fail the deploy
            raise FabkitError(f"fabric-cicd deploy failed: {error}", collected.messages) from error

        if uploads and not collected.messages:
            workspace_id = workspaces.resolve_workspace_id(credential, config, environment)
            upload_all(credential, workspace_id, uploads, args.upload_replace_existing)

    if collected.messages:
        raise FabkitError("fabric-cicd logged problems that fail the deploy.", collected.messages)
    print(f"Deploy to {environment} completed.")
    return 0
