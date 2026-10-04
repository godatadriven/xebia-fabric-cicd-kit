from typing import Any

import pytest
import yaml
from helpers import CONFIG, FabricRepo, assert_reported, find_replace

from fabkit.config import load_deploy_config
from fabkit.errors import FabkitError, Findings
from fabkit.parameters import check_parameters

GUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
HOST = "abc.datawarehouse.fabric.microsoft.com"


def check(repo: FabricRepo, parameter_text: str, **kwargs: Any) -> Findings:
    repo.write("src/parameter.yml", parameter_text)
    return check_parameters(load_deploy_config(repo.config), **kwargs)


def notebook_with(repo: FabricRepo, content: str) -> None:
    repo.write("src/nb.Notebook/notebook-content.py", content)


# A filled-in, environment-specific replacement passes.
def test_valid_parameters_pass(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B))

    assert findings.errors == []


# A missing environment key fails.
def test_missing_environment_key_fails(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_B))

    assert_reported(findings.errors, "missing", "PROD")


# A miscased key fails, names the right casing, and is not also reported missing.
def test_miscased_environment_key_fails(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_A, prod=GUID_B))

    assert_reported(findings.errors, "wrong casing", "PROD")
    assert not any("missing" in e for e in findings.errors), findings.errors


# An environment that config.yml does not define fails.
def test_unknown_environment_key_fails(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B, TEST=GUID_B))

    assert_reported(findings.errors, "unknown environment key", "TEST")


# _ALL_ next to another key fails.
def test_all_with_other_keys_fails(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, "x")

    findings = check(fabric_repo, find_replace("x", _ALL_="y", DEV="z"))

    assert_reported(findings.errors, "must be the only key")


# Each placeholder form fails.
@pytest.mark.parametrize("value", ["TODO-prod", "fixme later", "<prod workspace>", "XXXXXXXX-0000"])
def test_placeholder_fails(fabric_repo: FabricRepo, value: str) -> None:
    notebook_with(fabric_repo, "x")

    findings = check(fabric_repo, find_replace("x", DEV="y", PROD=value))

    assert_reported(findings.errors, "never filled in")


# A value shaped unlike its GUID find_value fails.
def test_guid_shape_mismatch_fails(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B[:-1]))

    assert_reported(findings.errors, "shape of a GUID")


# A value shaped unlike its Fabric hostname find_value fails.
def test_hostname_shape_mismatch_fails(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, HOST)

    findings = check(fabric_repo, find_replace(HOST, DEV=HOST, PROD="abc.database.windows.net"))

    assert_reported(findings.errors, "shape of a Fabric hostname")


# The same value in every environment fails and suggests _ALL_.
def test_identical_values_fail(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_B, PROD=GUID_B))

    assert_reported(findings.errors, "_ALL_")


# A find_value that occurs in no file fails by default.
def test_unmatched_find_value_fails(fabric_repo: FabricRepo) -> None:
    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B))

    assert_reported(findings.errors, "matches nothing")


# With unmatched_find_value="warning" the same case only warns.
def test_unmatched_find_value_can_warn(fabric_repo: FabricRepo) -> None:
    findings = check(
        fabric_repo,
        find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B),
        unmatched_find_value="warning",
    )

    assert findings.errors == []
    assert_reported(findings.warnings, "matches nothing")


# A regex find_value is not checked for a match.
def test_regex_find_value_is_not_matched(fabric_repo: FabricRepo) -> None:
    text = yaml.safe_dump(
        {
            "find_replace": [
                {
                    "find_value": "nothing-(here)",
                    "is_regex": "true",
                    "replace_value": {"DEV": "a", "PROD": "b"},
                }
            ]
        }
    )

    findings = check(fabric_repo, text)

    assert not any("matches nothing" in e for e in findings.errors), findings.errors


# A binary file in the repository does not crash the find_value scan.
def test_binary_file_is_skipped(fabric_repo: FabricRepo) -> None:
    (fabric_repo.root / "src/nb.Notebook/blob.bin").write_bytes(b"\xff\xfe\x00\x81")
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B))

    assert findings.errors == []


# In validate, a PLACEHOLDER value warns instead of failing.
def test_pending_warns_in_validate(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(fabric_repo, find_replace(GUID_A, DEV=GUID_B, PROD="PLACEHOLDER"))

    assert findings.errors == []
    assert_reported(findings.warnings, "PLACEHOLDER", "PROD")


# At deploy, a PLACEHOLDER fails for its own environment and not for the others.
def test_pending_fails_only_for_target(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)
    text = find_replace(GUID_A, DEV=GUID_B, PROD="PLACEHOLDER")

    to_dev = check(fabric_repo, text, target_environment="DEV")
    to_prod = check(fabric_repo, text, target_environment="PROD")

    assert to_dev.errors == []
    assert_reported(to_prod.errors, "PLACEHOLDER", "would be deployed to PROD")


# At deploy, a PLACEHOLDER under _ALL_ fails: every environment gets it.
def test_pending_under_all_fails_for_target(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, "x")

    findings = check(fabric_repo, find_replace("x", _ALL_="PLACEHOLDER"), target_environment="DEV")

    assert_reported(findings.errors, "PLACEHOLDER", "would be deployed to DEV")


# A pending connection in semantic_model_binding.models fails for its target.
def test_pending_model_binding_fails_for_target(fabric_repo: FabricRepo) -> None:
    text = yaml.safe_dump(
        {
            "semantic_model_binding": {
                "models": [
                    {
                        "semantic_model_name": "sales",
                        "connection_id": {"DEV": GUID_A, "PROD": "PLACEHOLDER"},
                    }
                ]
            }
        }
    )

    findings = check(fabric_repo, text, target_environment="PROD")

    assert_reported(findings.errors, "models[1] (sales)", "PLACEHOLDER")


# No parameter file configured means nothing to check.
def test_no_parameter_file_passes(fabric_repo: FabricRepo) -> None:
    (fabric_repo.root / "src/parameter.yml").unlink()
    fabric_repo.write("config.yml", CONFIG.replace("  parameter: src/parameter.yml\n", ""))

    findings = check_parameters(load_deploy_config(fabric_repo.config))

    assert findings.errors == []


# fabric-cicd's own validator is importable in the pinned version and rejects a bad file.
def test_fabric_cicd_parameter_validation_runs(fabric_repo: FabricRepo) -> None:
    findings = check(fabric_repo, find_replace(None, DEV="a", PROD="b"))

    assert_reported(findings.errors, "fabric-cicd rejected")


# A parameter.yml with invalid YAML is a FabkitError, not a traceback.
def test_invalid_yaml_parameter_file_fails(fabric_repo: FabricRepo) -> None:
    with pytest.raises(FabkitError, match="invalid YAML"):
        check(fabric_repo, "find_replace: [\n")
