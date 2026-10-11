from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from gitlab_ci_mcp.errors import InputError

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")
GITLAB_TOKEN = re.compile(
    r"\b(?:glpat|gldt|glrt|glrtr|glcbt|glptt|glft|glimt|glagent|glwt)-[A-Za-z0-9_-]+\b"
)
AUTHORIZATION = re.compile(r"(?im)^(authorization\s*:\s*)\S+.*$")
URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|PASSWORD|PASSWD|SECRET|API_KEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


class Redactor:
    def __init__(self, custom_patterns: tuple[str, ...] = ()) -> None:
        self._custom_patterns = tuple(re.compile(pattern) for pattern in custom_patterns)

    def clean(self, text: str) -> str:
        redacted = ANSI_ESCAPE.sub("", text)
        redacted = GITLAB_TOKEN.sub("[REDACTED]", redacted)
        redacted = AUTHORIZATION.sub(r"\1[REDACTED]", redacted)
        redacted = URL_CREDENTIALS.sub(r"\1[REDACTED]@", redacted)
        redacted = SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
        for pattern in self._custom_patterns:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted


@dataclass(frozen=True, slots=True)
class LogBounds:
    line_limit: int
    byte_limit: int
    search_text: str | None = None


def bounded_log(
    raw_log: str,
    redactor: Redactor,
    bounds: LogBounds,
) -> dict[str, object]:
    cleaned_lines = redactor.clean(raw_log).splitlines()
    cleaned_lines = _matching_lines(cleaned_lines, bounds.search_text)
    selected_lines = cleaned_lines[-bounds.line_limit :]
    encoded = "\n".join(selected_lines).encode("utf-8")
    truncated_bytes = len(encoded) > bounds.byte_limit
    if truncated_bytes:
        encoded = encoded[-bounds.byte_limit :]
        encoded = encoded.decode("utf-8", errors="ignore").encode("utf-8")
    rendered_text = encoded.decode("utf-8")
    return {
        "text": rendered_text,
        "line_count": len(rendered_text.splitlines()),
        "truncated": len(cleaned_lines) > bounds.line_limit or truncated_bytes,
        "redaction": "best_effort",
    }


def _matching_lines(lines: list[str], search_text: str | None) -> list[str]:
    if not search_text:
        return lines
    needle = search_text.casefold()
    return [line for line in lines if needle in line.casefold()]


def validated_artifact_path(raw_path: str) -> str:
    artifact_path = PurePosixPath(raw_path)
    if not raw_path or artifact_path.is_absolute() or ".." in artifact_path.parts:
        raise InputError("artifact_path must be a relative path without parent traversal")
    if any(part in {"", "."} for part in artifact_path.parts):
        raise InputError("artifact_path contains an invalid segment")
    return artifact_path.as_posix()


def decoded_text(raw_content: bytes) -> str:
    if b"\x00" in raw_content:
        raise InputError("artifact content appears to be binary")
    try:
        return raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("artifact content is not UTF-8 text") from exc
