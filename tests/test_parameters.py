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


def key_value_replace(**values: Any) -> str:
    entry = {"find_key": "$.properties.server", "replace_value": values}
    return yaml.safe_dump({"key_value_replace": [entry]}, sort_keys=False)


def spark_pool(**names: str) -> str:
    replace_value = {env: {"type": "Capacity", "name": name} for env, name in names.items()}
    entry = {"instance_pool_id": GUID_A, "replace_value": replace_value}
    return yaml.safe_dump({"spark_pool": [entry]}, sort_keys=False)


# A PLACEHOLDER under a lowercase _all_ fails for the target: fabric-cicd matches _ALL_ in any case.
def test_pending_under_lowercase_all_fails_for_target(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, "x")

    findings = check(fabric_repo, find_replace("x", _all_="PLACEHOLDER"), target_environment="PROD")

    assert_reported(findings.errors, "PLACEHOLDER", "would be deployed to PROD")


# A PLACEHOLDER in key_value_replace or spark_pool fails for its target.
@pytest.mark.parametrize("build", [key_value_replace, spark_pool])
def test_pending_in_other_parameters_fails_for_target(fabric_repo: FabricRepo, build: Any) -> None:
    findings = check(fabric_repo, build(DEV="dev", PROD="PLACEHOLDER"), target_environment="PROD")

    assert_reported(findings.errors, "PLACEHOLDER", "would be deployed to PROD")


# In validate, a PLACEHOLDER in key_value_replace or spark_pool warns.
@pytest.mark.parametrize("build", [key_value_replace, spark_pool])
def test_pending_in_other_parameters_warns_in_validate(fabric_repo: FabricRepo, build: Any) -> None:
    findings = check(fabric_repo, build(DEV="dev", PROD="PLACEHOLDER"))

    assert findings.errors == []
    assert_reported(findings.warnings, "PLACEHOLDER", "PROD")


# A missing environment key in key_value_replace or spark_pool fails.
@pytest.mark.parametrize("build", [key_value_replace, spark_pool])
def test_missing_environment_key_in_other_parameters_fails(
    fabric_repo: FabricRepo, build: Any
) -> None:
    findings = check(fabric_repo, build(DEV="dev"))

    assert_reported(findings.errors, "missing", "PROD")


# A dynamic replace_value starting with $ is not shape-checked.
def test_dynamic_replace_value_skips_shape_check(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, GUID_A)

    findings = check(
        fabric_repo, find_replace(GUID_A, DEV="$items.Lakehouse.lh.$id", PROD="$workspace.$id")
    )

    assert not any("shape" in e for e in findings.errors), findings.errors


# A find_value starting with $ is not checked for a match.
def test_dynamic_find_value_is_not_matched(fabric_repo: FabricRepo) -> None:
    findings = check(fabric_repo, find_replace("$workspace.$id", DEV="a", PROD="b"))

    assert not any("matches nothing" in e for e in findings.errors), findings.errors


# With ignore_case, a find_value matches a file in another casing.
def test_ignore_case_find_value_matches_any_casing(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, "server = 'abc'")
    entry = {"find_value": "ABC", "ignore_case": "true", "replace_value": {"DEV": "a", "PROD": "b"}}

    findings = check(fabric_repo, yaml.safe_dump({"find_replace": [entry]}))

    assert not any("matches nothing" in e for e in findings.errors), findings.errors


# is_regex "false" is not a regex: the find_value is still checked for a match.
def test_is_regex_false_is_matched(fabric_repo: FabricRepo) -> None:
    entry = {
        "find_value": "nothing",
        "is_regex": "false",
        "replace_value": {"DEV": "a", "PROD": "b"},
    }

    findings = check(fabric_repo, yaml.safe_dump({"find_replace": [entry]}))

    assert_reported(findings.errors, "matches nothing")


# The legacy semantic_model_binding list passes.
def test_legacy_model_binding_passes(fabric_repo: FabricRepo) -> None:
    binding = [{"connection_id": GUID_A, "semantic_model_name": "sales"}]

    findings = check(fabric_repo, yaml.safe_dump({"semantic_model_binding": binding}))

    assert findings.errors == []


# A PLACEHOLDER in a legacy binding mapping fails for its target.
def test_pending_legacy_model_binding_fails_for_target(fabric_repo: FabricRepo) -> None:
    binding = [
        {"connection_id": {"DEV": GUID_A, "PROD": "PLACEHOLDER"}, "semantic_model_name": "sales"}
    ]

    findings = check(
        fabric_repo, yaml.safe_dump({"semantic_model_binding": binding}), target_environment="PROD"
    )

    assert_reported(findings.errors, "semantic_model_binding[1] (sales)", "PLACEHOLDER")


# A parameter file with another name is not counted as a match for its own find_values.
def test_parameter_file_in_use_is_not_matched(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("src/parameter.yml", "src/parameter-prod.yml"))
    fabric_repo.write("src/parameter-prod.yml", find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B))

    findings = check_parameters(load_deploy_config(fabric_repo.config))

    assert_reported(findings.errors, "matches nothing")


# A word that contains TODO is not a placeholder.
def test_todo_inside_a_word_is_not_a_placeholder(fabric_repo: FabricRepo) -> None:
    notebook_with(fabric_repo, "x")

    findings = check(fabric_repo, find_replace("x", DEV="photodocs", PROD="y"))

    assert not any("never filled in" in e for e in findings.errors), findings.errors


# A parameter file that is not UTF-8 is a FabkitError, not a traceback.
def test_non_utf8_parameter_file_fails(fabric_repo: FabricRepo) -> None:
    (fabric_repo.root / "src/parameter.yml").write_bytes(b"find_replace: \x93x\x94\n")

    with pytest.raises(FabkitError, match="invalid YAML"):
        check_parameters(load_deploy_config(fabric_repo.config))
