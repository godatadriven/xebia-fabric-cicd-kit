import os
from typing import Any

import pytest
import yaml
from helpers import CONFIG, FabricRepo, assert_reported, find_replace, platform_json

from fabkit.config import load_deploy_config
from fabkit.errors import FabkitError, Findings
from fabkit.parameters import check_parameters

GUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
GUID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
GUID_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"
HOST = "abc.datawarehouse.fabric.microsoft.com"
ENV_FLAG = "features: [enable_environment_variable_replacement]\n"


@pytest.fixture
def repo(fabric_repo: FabricRepo) -> FabricRepo:
    """Notebooks nb (holding GUID_A and HOST) and other, model sales, and files outside scope."""
    fabric_repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, SemanticModel]"))
    fabric_repo.write("src/nb.Notebook/notebook-content.py", f"{GUID_A}\n{HOST}\nserver = 'abc'\n")
    (fabric_repo.root / "src/nb.Notebook/blob.bin").write_bytes(b"\xff\xfe\x00\x81")
    fabric_repo.write("src/other.Notebook/.platform", platform_json("Notebook", "other", GUID_B))
    fabric_repo.write("src/other.Notebook/notebook-content.py", "no ids here\n")
    fabric_repo.write(
        "src/sales.SemanticModel/.platform", platform_json("SemanticModel", "sales", GUID_C)
    )
    fabric_repo.write("src/r.Report/.platform", platform_json("Report", "r", GUID_D))
    fabric_repo.write("src/r.Report/report.json", "only-in-report")
    fabric_repo.write("src/notes.md", "only-in-notes")
    return fabric_repo


def check(repo: FabricRepo, files: str | dict[str, str], **kwargs: Any) -> Findings:
    """Write the parameter file, or several files by path, and check the parameters."""
    for path, text in ({"src/parameter.yml": files} if isinstance(files, str) else files).items():
        repo.write(path, text)
    return check_parameters(load_deploy_config(repo.config), **kwargs)


def entries(parameter: str, *items: dict[str, Any]) -> str:
    return yaml.safe_dump({parameter: list(items)}, sort_keys=False)


def guid_entry(**fields: Any) -> str:
    """One find_replace entry for GUID_A, with extra fields."""
    replace = {"DEV": GUID_A, "PROD": GUID_B}
    return entries("find_replace", {"find_value": GUID_A, "replace_value": replace, **fields})


def key_value_replace(**values: str) -> str:
    return entries("key_value_replace", {"find_key": "$.server", "replace_value": values})


def models(name: str = "sales", **connection_ids: str) -> str:
    """A semantic_model_binding in the current format, for one model."""
    model = {"semantic_model_name": name, "connection_id": connection_ids or {"_ALL_": GUID_A}}
    return yaml.safe_dump({"semantic_model_binding": {"models": [model]}})


def legacy(name: str = "sales", connection_id: Any = GUID_A) -> str:
    """A semantic_model_binding in the legacy list format."""
    entry = {"semantic_model_name": name, "connection_id": connection_id}
    return yaml.safe_dump({"semantic_model_binding": [entry]})


def extend(template: str) -> dict[str, str]:
    """A parameter.yml that takes all its content from a template under extend."""
    return {"src/template.yml": template, "src/parameter.yml": "extend:\n  - template.yml\n"}


# Each problem in a parameter file fails, with a message naming it.
@pytest.mark.parametrize(
    ("files", "fragments"),
    [
        pytest.param(find_replace(GUID_A, DEV=GUID_B), ("missing", "PROD"), id="missing-key"),
        pytest.param(
            find_replace(GUID_A, DEV=GUID_A, prod=GUID_B), ("wrong casing", "PROD"), id="casing"
        ),
        pytest.param(
            find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B, TEST=GUID_B),
            ("unknown environment key", "TEST"),
            id="unknown-key",
        ),
        pytest.param(
            find_replace(GUID_A, _ALL_=GUID_B, DEV=GUID_A),
            ("can only be used alone",),
            id="all-with-others",
        ),
        *(
            pytest.param(
                find_replace(GUID_A, DEV=GUID_A, PROD=value), ("never filled in",), id=value
            )
            for value in ("TODO-prod", "<prod workspace>")
        ),
        pytest.param(
            find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B[:-1]), ("shape of a GUID",), id="guid"
        ),
        pytest.param(find_replace(GUID_A, DEV=GUID_B, PROD=GUID_B), ("_ALL_",), id="same-value"),
        pytest.param(
            find_replace("nowhere", DEV="a", PROD="b"), ("matches nothing",), id="no-match"
        ),
        pytest.param(
            find_replace("only-in-notes", DEV="a", PROD="b"), ("matches nothing",), id="not-in-item"
        ),
        pytest.param(
            find_replace("only-in-report", DEV="a", PROD="b"),
            ("matches nothing",),
            id="item-out-of-scope",
        ),
        pytest.param(guid_entry(item_name="other"), ("matches nothing",), id="other-item"),
        pytest.param(guid_entry(item_name="nb_typo"), ("'nb_typo' not found",), id="no-such-item"),
        pytest.param(
            guid_entry(item_type="NotAType"),
            ("Item type 'NotAType' not in scope",),
            id="logged-by-fabric-cicd",
        ),
        pytest.param(
            find_replace(None, DEV="a", PROD="b"), ("fabric-cicd rejected",), id="rejected"
        ),
        pytest.param(key_value_replace(DEV="dev"), ("missing", "PROD"), id="key-value-replace"),
        pytest.param(models("salse"), ("'salse'", "not in the repository"), id="unknown-model"),
        pytest.param(
            legacy(connection_id={"DEV": GUID_A, "PROD": GUID_B}),
            ("not supported in the legacy format",),
            id="legacy-mapping",
        ),
        pytest.param(
            extend(find_replace(GUID_A, DEV="TODO", PROD=GUID_B)),
            ("never filled in",),
            id="extend-template",
        ),
    ],
)
def test_parameter_problem_fails(
    repo: FabricRepo, files: str | dict[str, str], fragments: tuple[str, ...]
) -> None:
    assert_reported(check(repo, files).errors, *fragments)


# Valid entries, and entries fabkit cannot check, pass.
@pytest.mark.parametrize(
    "files",
    [
        pytest.param(find_replace(GUID_A, DEV=GUID_A, PROD=GUID_B), id="valid"),
        pytest.param(
            entries(
                "find_replace",
                {
                    "find_value": "no-(match)",
                    "is_regex": "true",
                    "replace_value": {"DEV": "a", "PROD": "b"},
                },
            ),
            id="regex",
        ),
        pytest.param(find_replace("$workspace.$id", DEV="a", PROD="b"), id="dynamic-find"),
        pytest.param(
            find_replace(GUID_A, DEV="$items.Lakehouse.lh.$id", PROD="$workspace.$id"),
            id="dynamic-replace",
        ),
        pytest.param(
            entries(
                "find_replace",
                {
                    "find_value": "ABC",
                    "ignore_case": "true",
                    "replace_value": {"DEV": "a", "PROD": "b"},
                },
            ),
            id="ignore-case",
        ),
        pytest.param(guid_entry(item_name=["other", "nb"]), id="item-list"),
        pytest.param(guid_entry(file_path="**/notebook-content.py"), id="file-glob"),
        pytest.param(find_replace("server", DEV="photodocs", PROD="b"), id="todo-in-word"),
        pytest.param(models(), id="known-model"),
        pytest.param(legacy(), id="legacy-known-model"),
    ],
)
def test_valid_parameters_pass(repo: FabricRepo, files: str) -> None:
    assert check(repo, files).errors == []


# A PLACEHOLDER warns in validate, and fails a deploy only to an environment that receives it.
@pytest.mark.parametrize(
    ("files", "target", "fails"),
    [
        (find_replace(GUID_A, DEV=GUID_A, PROD="PLACEHOLDER"), None, False),
        (find_replace(GUID_A, DEV=GUID_A, PROD="PLACEHOLDER"), "DEV", False),
        (find_replace(GUID_A, DEV=GUID_A, PROD="PLACEHOLDER"), "PROD", True),
        (find_replace(GUID_A, _ALL_="PLACEHOLDER"), "DEV", True),
        (find_replace(GUID_A, _all_="PLACEHOLDER"), "PROD", True),
        (key_value_replace(DEV="dev", PROD="PLACEHOLDER"), "PROD", True),
        (models(DEV=GUID_A, PROD="PLACEHOLDER"), "PROD", True),
        (extend(find_replace(GUID_A, DEV=GUID_A, PROD="PLACEHOLDER")), "PROD", True),
    ],
)
def test_placeholder_value(
    repo: FabricRepo, files: str | dict[str, str], target: str | None, fails: bool
) -> None:
    findings = check(repo, files, target_environment=target)

    if fails:
        assert_reported(findings.errors, "PLACEHOLDER", f"would be deployed to {target}")
    else:
        assert not any("PLACEHOLDER" in e for e in findings.errors), findings.errors
    if target is None:
        assert_reported(findings.warnings, "PLACEHOLDER", "PROD")


# With unmatched_find_value="warning", an unmatched find_value only warns.
def test_unmatched_find_value_can_warn(repo: FabricRepo) -> None:
    findings = check(
        repo, find_replace("nowhere", DEV="a", PROD="b"), unmatched_find_value="warning"
    )

    assert findings.errors == []
    assert_reported(findings.warnings, "matches nothing")


# A parameter file shared by environments is checked against each environment's folder.
def test_checked_per_repository_directory(repo: FabricRepo) -> None:
    repo.write(
        "config.yml",
        repo.config.read_text().replace(
            "  repository_directory: src\n",
            "  repository_directory:\n    DEV: src\n    PROD: src_prod\n",
        ),
    )
    repo.write("src_prod/nb.Notebook/.platform", platform_json("Notebook", "nb", GUID_B))
    text = (
        entries(
            "find_replace", {"find_value": GUID_A, "replace_value": {"DEV": GUID_A, "PROD": GUID_B}}
        )
        + models()
    )

    errors = check(repo, text).errors

    assert_reported(errors, "matches nothing", "PROD")
    assert_reported(errors, "'sales'", "src_prod/")
    assert not any("matches nothing" in e and "DEV" in e for e in errors), errors


# Without a parameter file there is nothing to check.
def test_no_parameter_file_passes(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("  parameter: src/parameter.yml\n", ""))

    assert check_parameters(load_deploy_config(fabric_repo.config)).errors == []


# An unreadable parameter file is a FabkitError, not a traceback.
@pytest.mark.parametrize("content", [b"find_replace: [\n", b"find_replace: \x93x\x94\n"])
def test_unreadable_parameter_file_fails(fabric_repo: FabricRepo, content: bytes) -> None:
    (fabric_repo.root / "src/parameter.yml").write_bytes(content)

    with pytest.raises(FabkitError, match="invalid YAML"):
        check_parameters(load_deploy_config(fabric_repo.config))


PROD_LAKEHOUSE = find_replace(GUID_A, DEV=GUID_A, PROD="$ENV:PROD_LAKEHOUSE")
ENV_CONNECTIONS = models(DEV="$ENV:DEV_CONNECTION", PROD="$ENV:PROD_CONNECTION")


# An $ENV: value needs its variable only for the deploy target; a connection_id needs all of them.
@pytest.mark.parametrize(
    ("files", "flag", "target", "variables", "fragment"),
    [
        pytest.param(PROD_LAKEHOUSE, True, None, {}, None, id="validate"),
        pytest.param(PROD_LAKEHOUSE, True, "PROD", {}, "could not be resolved", id="target-unset"),
        pytest.param(
            PROD_LAKEHOUSE, True, "PROD", {"PROD_LAKEHOUSE": GUID_B}, None, id="target-set"
        ),
        pytest.param(PROD_LAKEHOUSE, True, "DEV", {}, None, id="other-target"),
        pytest.param(ENV_CONNECTIONS, True, None, {}, None, id="connection-validate"),
        pytest.param(
            ENV_CONNECTIONS,
            True,
            "PROD",
            {"PROD_CONNECTION": GUID_A},
            "DEV",
            id="connection-deploy",
        ),
        pytest.param(
            models(_ALL_="$ENV:CONNECTION"),
            True,
            "PROD",
            {"CONNECTION": GUID_A},
            None,
            id="connection-all",
        ),
    ],
)
def test_env_reference(
    repo: FabricRepo,
    monkeypatch: pytest.MonkeyPatch,
    files: str | dict[str, str],
    flag: bool,
    target: str | None,
    variables: dict[str, str],
    fragment: str | None,
) -> None:
    repo.write("config.yml", repo.config.read_text() + (ENV_FLAG if flag else ""))
    for name in ("PROD_LAKEHOUSE", "DEV_CONNECTION", "PROD_CONNECTION", "CONNECTION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    errors = check(repo, files, target_environment=target).errors

    if fragment is None:
        assert errors == []
    else:
        assert_reported(errors, fragment)
    if not variables:
        assert "PROD_LAKEHOUSE" not in os.environ, "a stand-in variable was left behind"
