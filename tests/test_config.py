from __future__ import annotations

import pytest

from gitlab_ci_mcp.config import ConfigurationError, Settings


def test_missing_token_rejects_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)

    with pytest.raises(ConfigurationError, match="GITLAB_TOKEN is required"):
        Settings.from_env()


@pytest.mark.parametrize("raw_value", ["true", "1", "yes", "on"])
def test_write_enablement_accepts_explicit_truthy_values(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "test-token")
    monkeypatch.setenv("GITLAB_ENABLE_WRITES", raw_value)

    assert Settings.from_env().enable_writes is True


def test_url_with_embedded_credentials_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "test-token")
    monkeypatch.setenv("GITLAB_URL", "https://user:pass@gitlab.example.com")

    with pytest.raises(ConfigurationError, match="must not contain credentials"):
        Settings.from_env()


def test_artifact_limit_cannot_exceed_response_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "test-token")
    monkeypatch.setenv("GITLAB_MAX_RESPONSE_BYTES", "10")
    monkeypatch.setenv("GITLAB_MAX_ARTIFACT_BYTES", "11")

    with pytest.raises(ConfigurationError, match="cannot exceed"):
        Settings.from_env()
