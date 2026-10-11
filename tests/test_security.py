from __future__ import annotations

import pytest

from gitlab_ci_mcp.errors import InputError
from gitlab_ci_mcp.security import (
    LogBounds,
    Redactor,
    bounded_log,
    decoded_text,
    validated_artifact_path,
)


@pytest.mark.parametrize(
    ("raw_text", "visible_text", "secret_text"),
    [
        ("token=glpat-abcdefghijklmnopqrstuvwxyz", "token=[REDACTED]", "glpat-abc"),
        ("Authorization: Bearer secret", "Authorization: [REDACTED]", "secret"),
        ("PASSWORD=hunter2", "PASSWORD=[REDACTED]", "hunter2"),
        (
            "fetch https://user:secret@example.com/path",
            "https://[REDACTED]@example.com/path",
            "user:secret",
        ),
    ],
)
def test_redactor_removes_common_secret_shapes(
    raw_text: str, visible_text: str, secret_text: str
) -> None:
    cleaned = Redactor().clean(raw_text)

    assert visible_text in cleaned
    assert secret_text not in cleaned


def test_bounded_log_filters_literal_text_and_reports_truncation() -> None:
    raw_log = "first\nERROR one\nsecond\nERROR two\nERROR three"

    log = bounded_log(raw_log, Redactor(), LogBounds(2, 100, "error"))

    assert log == {
        "text": "ERROR two\nERROR three",
        "line_count": 2,
        "truncated": True,
        "redaction": "best_effort",
    }


@pytest.mark.parametrize("artifact_path", ["", "/root.txt", "../secret", "a/../../secret"])
def test_unsafe_artifact_paths_are_rejected(artifact_path: str) -> None:
    with pytest.raises(InputError):
        validated_artifact_path(artifact_path)


@pytest.mark.parametrize("raw_content", [b"binary\x00payload", b"\xff\xfe"])
def test_binary_or_non_utf8_artifacts_are_rejected(raw_content: bytes) -> None:
    with pytest.raises(InputError):
        decoded_text(raw_content)
