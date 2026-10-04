import json
from pathlib import Path

from helpers import assert_reported

from fabkit.platform_files import check_logical_ids


def platform(root: Path, item: str, logical_id: object) -> None:
    path = root / item / ".platform"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"config": {"logicalId": logical_id}}), encoding="utf-8")


# Unique logicalIds pass; files under .git do not count.
def test_unique_ids_pass(tmp_path: Path) -> None:
    platform(tmp_path, "a.Notebook", "1")
    platform(tmp_path, "b.Notebook", "2")
    platform(tmp_path, ".git/x.Notebook", "1")
    assert check_logical_ids(tmp_path).errors == []


# Two items sharing a logicalId fail, naming both files.
def test_duplicate_ids_fail(tmp_path: Path) -> None:
    platform(tmp_path, "a.Notebook", "1")
    platform(tmp_path, "b.Notebook", "1")
    assert_reported(check_logical_ids(tmp_path).errors, "a.Notebook", "b.Notebook")


# Invalid JSON, a missing logicalId and a non-string logicalId each fail.
def test_invalid_files_fail(tmp_path: Path) -> None:
    (tmp_path / "a.Notebook").mkdir()
    (tmp_path / "a.Notebook/.platform").write_text("{", encoding="utf-8")
    platform(tmp_path, "b.Notebook", None)
    platform(tmp_path, "c.Notebook", 3)
    assert len(check_logical_ids(tmp_path).errors) == 3


# A .platform file holding a JSON array is reported, not a crash.
def test_array_json_fails(tmp_path: Path) -> None:
    (tmp_path / "a.Notebook").mkdir()
    (tmp_path / "a.Notebook/.platform").write_text("[]", encoding="utf-8")
    assert len(check_logical_ids(tmp_path).errors) == 1


# A .platform file that is not UTF-8 is reported as invalid, not a crash.
def test_non_utf8_file_fails(tmp_path: Path) -> None:
    (tmp_path / "a.Notebook").mkdir()
    (tmp_path / "a.Notebook/.platform").write_bytes(b"\xff\xfe")
    assert_reported(check_logical_ids(tmp_path).errors, "a.Notebook", "invalid JSON")
