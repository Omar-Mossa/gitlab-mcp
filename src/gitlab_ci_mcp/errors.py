from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GitLabError(Exception):
    code: str
    message: str
    status: int | None = None
    retry_after: float | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.status is not None:
            error["status"] = self.status
        if self.retry_after is not None:
            error["retry_after"] = self.retry_after
        return error


class UnsafeRedirectError(GitLabError):
    def __init__(self) -> None:
        super().__init__("unsafe_redirect", "GitLab redirected the request to a different origin")


class ResponseTooLargeError(GitLabError):
    def __init__(self, limit: int) -> None:
        super().__init__("response_too_large", f"GitLab response exceeded the {limit}-byte limit")


class CompatibilityError(GitLabError):
    def __init__(self, feature: str, minimum_version: str) -> None:
        message = f"{feature} requires GitLab {minimum_version} or newer"
        super().__init__("unsupported_version", message)


class InputError(GitLabError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_input", message)
