from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    gitlab_url: str
    token: str
    enable_writes: bool = False
    timeout_seconds: float = 20.0
    max_response_bytes: int = 2_000_000
    max_artifact_bytes: int = 100_000
    max_log_lines: int = 1_000
    redact_patterns: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("GITLAB_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("GITLAB_TOKEN is required")
        settings = cls(
            gitlab_url=_validated_url(os.getenv("GITLAB_URL", "https://gitlab.com")),
            token=token,
            enable_writes=_env_bool("GITLAB_ENABLE_WRITES", False),
            timeout_seconds=_env_float("GITLAB_TIMEOUT_SECONDS", 20.0),
            max_response_bytes=_env_int("GITLAB_MAX_RESPONSE_BYTES", 2_000_000),
            max_artifact_bytes=_env_int("GITLAB_MAX_ARTIFACT_BYTES", 100_000),
            max_log_lines=_env_int("GITLAB_MAX_LOG_LINES", 1_000),
            redact_patterns=_redact_patterns(os.getenv("GITLAB_REDACT_PATTERNS", "")),
        )
        _validate_limits(settings)
        return settings


def _validated_url(raw_url: str) -> str:
    normalized = raw_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("GITLAB_URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("GITLAB_URL must not contain credentials, a query, or a fragment")
    return normalized


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    if raw_value.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw_value.lower() in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    try:
        return default if raw_value is None else int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    try:
        return default if raw_value is None else float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _redact_patterns(raw_patterns: str) -> tuple[str, ...]:
    patterns = tuple(pattern.strip() for pattern in raw_patterns.split(",") if pattern.strip())
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigurationError(f"Invalid GITLAB_REDACT_PATTERNS expression: {exc}") from exc
    return patterns


def _validate_limits(settings: Settings) -> None:
    positive_limits = {
        "GITLAB_TIMEOUT_SECONDS": settings.timeout_seconds,
        "GITLAB_MAX_RESPONSE_BYTES": settings.max_response_bytes,
        "GITLAB_MAX_ARTIFACT_BYTES": settings.max_artifact_bytes,
        "GITLAB_MAX_LOG_LINES": settings.max_log_lines,
    }
    invalid = next((name for name, limit in positive_limits.items() if limit <= 0), None)
    if invalid:
        raise ConfigurationError(f"{invalid} must be greater than zero")
    if settings.max_artifact_bytes > settings.max_response_bytes:
        raise ConfigurationError(
            "GITLAB_MAX_ARTIFACT_BYTES cannot exceed GITLAB_MAX_RESPONSE_BYTES"
        )
