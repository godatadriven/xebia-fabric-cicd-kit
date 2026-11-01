import io
from pathlib import Path

import pytest

from fabkit.errors import Findings, Issue, print_findings, summarize_findings


def report(findings: Findings) -> str:
    stream = io.StringIO()
    print_findings(findings, stream)
    return stream.getvalue()


# Issues are grouped by file, sorted by path, errors before warnings, issues in no file last.
def test_findings_grouped_by_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    findings = Findings(
        errors=[
            Issue(tmp_path / "src/parameter.yml", "bad key"),
            Issue(None, "no file"),
            Issue(tmp_path / "config.yml", "no skip"),
        ],
        warnings=[Issue(tmp_path / "config.yml", "pending")],
    )

    assert report(findings) == (
        "config.yml\n"
        "  error    no skip\n"
        "  warning  pending\n"
        "\n"
        "src/parameter.yml\n"
        "  error    bad key\n"
        "\n"
        "Other\n"
        "  error    no file\n"
        "\n"
    )


# A repeated issue is printed once; lines after the first are indented under the message.
def test_findings_deduplicated_and_indented(tmp_path: Path) -> None:
    issue = Issue(tmp_path / "config.yml", "first\nsecond")

    assert report(Findings(errors=[issue, issue])).splitlines()[1:3] == [
        "  error    first",
        "           second",
    ]


# The summary counts unique errors, warnings and files.
@pytest.mark.parametrize(
    ("findings", "summary"),
    [
        (Findings(errors=[Issue(Path("a"), "x")]), "1 error in 1 file"),
        (
            Findings(
                errors=[Issue(Path("a"), "x"), Issue(Path("a"), "x"), Issue(Path("b"), "y")],
                warnings=[Issue(Path("a"), "z")],
            ),
            "2 errors and 1 warning in 2 files",
        ),
        (Findings(errors=[Issue(None, "x")]), "1 error"),
    ],
)
def test_summary(findings: Findings, summary: str) -> None:
    assert summarize_findings(findings) == summary
