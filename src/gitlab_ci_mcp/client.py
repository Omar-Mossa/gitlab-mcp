from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, cast
from urllib.parse import quote, urljoin, urlparse

import httpx

from gitlab_ci_mcp.config import Settings
from gitlab_ci_mcp.errors import (
    CompatibilityError,
    GitLabError,
    ResponseTooLargeError,
    UnsafeRedirectError,
)

TRANSIENT_STATUSES = {429, 502, 503, 504}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_ATTEMPTS = 3
MAX_REDIRECTS = 3


@dataclass(frozen=True, slots=True)
class GitLabRequest:
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    json_body: Any | None = None


class GitLabClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client
        self._api_url = f"{settings.gitlab_url}/api/v4/"
        self._origin = _origin(settings.gitlab_url)

    @staticmethod
    def project_path(project: str) -> str:
        return quote(project, safe="")

    async def json(self, request: GitLabRequest) -> Any:
        response = await self._request_with_retries(request)
        raw_content = await self._read_limited(response, self._settings.max_response_bytes)
        try:
            return json.loads(raw_content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitLabError("invalid_response", "GitLab returned invalid JSON") from exc

    async def text(self, request: GitLabRequest) -> str:
        response = await self._request_with_retries(request)
        raw_content = await self._read_limited(response, self._settings.max_response_bytes)
        return raw_content.decode(response.encoding or "utf-8", errors="replace")

    async def json_object(self, request: GitLabRequest) -> dict[str, Any]:
        payload = await self.json(request)
        if not isinstance(payload, dict):
            raise GitLabError("invalid_response", "GitLab response was not an object")
        return cast(dict[str, Any], payload)

    async def json_object_list(self, request: GitLabRequest) -> list[dict[str, Any]]:
        return _json_object_page(await self.json(request))

    async def raw_bytes(self, request: GitLabRequest, limit: int) -> bytes:
        response = await self._request_with_retries(request)
        return await self._read_limited(response, limit)

    async def paginated_json(
        self, request: GitLabRequest, max_items: int = 500
    ) -> list[dict[str, Any]]:
        params = dict(request.params)
        page_size = min(int(params.get("per_page", 100)), 100)
        page = int(params.get("page", 1))
        collected: list[dict[str, Any]] = []
        while len(collected) < max_items:
            params.update({"page": page, "per_page": page_size})
            page_payload = await self.json(
                GitLabRequest(request.method, request.path, dict(params), request.json_body)
            )
            page_entries = _json_object_page(page_payload)
            collected.extend(page_entries[: max_items - len(collected)])
            if len(page_payload) < page_size:
                break
            page += 1
        return collected

    async def ensure_version(self, feature: str, minimum: tuple[int, int]) -> None:
        payload = await self.json_object(GitLabRequest("GET", "version"))
        raw_version = str(payload.get("version", ""))
        current = _major_minor(raw_version)
        if current is None or current < minimum:
            raise CompatibilityError(feature, f"{minimum[0]}.{minimum[1]}")

    async def _request_with_retries(self, request: GitLabRequest) -> httpx.Response:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._request_following_safe_redirects(request)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise GitLabError("network_error", "GitLab request failed") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code not in TRANSIENT_STATUSES or attempt == MAX_ATTEMPTS - 1:
                return await self._validated_response(response)
            await response.aclose()
            await asyncio.sleep(_retry_delay(response, attempt))
        raise AssertionError("retry loop exhausted")

    async def _request_following_safe_redirects(self, request: GitLabRequest) -> httpx.Response:
        url = urljoin(self._api_url, request.path.lstrip("/"))
        method = request.method
        for _ in range(MAX_REDIRECTS + 1):
            outgoing = self._http.build_request(
                method,
                url,
                params=request.params,
                json=request.json_body,
                headers={"PRIVATE-TOKEN": self._settings.token},
            )
            response = await self._http.send(outgoing, stream=True)
            if response.status_code not in REDIRECT_STATUSES:
                return response
            try:
                url, method = self._redirect_target(response, url, method)
            finally:
                await response.aclose()
        raise GitLabError("too_many_redirects", "GitLab response exceeded the redirect limit")

    def _redirect_target(
        self, response: httpx.Response, current_url: str, method: str
    ) -> tuple[str, str]:
        location = response.headers.get("location", "")
        target_url = urljoin(current_url, location)
        if not location or _origin(target_url) != self._origin:
            raise UnsafeRedirectError()
        redirected_method = "GET" if response.status_code == 303 else method
        return target_url, redirected_method

    @staticmethod
    async def _validated_response(response: httpx.Response) -> httpx.Response:
        if response.is_success:
            return response
        error = _error_from_response(response)
        await response.aclose()
        raise error

    @staticmethod
    async def _read_limited(response: httpx.Response, limit: int) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > limit:
            await response.aclose()
            raise ResponseTooLargeError(limit)
        chunks: list[bytes] = []
        byte_count = 0
        try:
            async for chunk in response.aiter_bytes():
                byte_count += len(chunk)
                if byte_count > limit:
                    raise ResponseTooLargeError(limit)
                chunks.append(chunk)
        finally:
            await response.aclose()
        return b"".join(chunks)


def _major_minor(raw_version: str) -> tuple[int, int] | None:
    parts = raw_version.split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return int(parts[0]), int(parts[1])


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlparse(url)
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname, parsed.port or default_port


def _json_object_page(page_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(page_payload, list) or any(
        not isinstance(page_entry, dict) for page_entry in page_payload
    ):
        raise GitLabError("invalid_response", "GitLab pagination response was not an object list")
    return cast(list[dict[str, Any]], page_payload)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    raw_retry_after = response.headers.get("retry-after")
    if raw_retry_after:
        try:
            return min(float(raw_retry_after), 30.0)
        except ValueError:
            try:
                seconds = (
                    parsedate_to_datetime(raw_retry_after)
                    - parsedate_to_datetime(response.headers["date"])
                ).total_seconds()
                return min(max(seconds, 0.0), 30.0)
            except (KeyError, TypeError, ValueError):
                pass
    return float(0.25 * (2**attempt))


def _error_from_response(response: httpx.Response) -> GitLabError:
    errors = {
        400: ("bad_request", "GitLab rejected the request"),
        401: ("unauthorized", "GitLab authentication failed"),
        403: ("forbidden", "The token cannot access this GitLab resource"),
        404: ("not_found", "The GitLab resource was not found"),
        409: ("conflict", "The GitLab resource is in a conflicting state"),
        429: ("rate_limited", "GitLab rate limit exceeded"),
    }
    code, message = errors.get(response.status_code, ("gitlab_error", "GitLab request failed"))
    retry_after = _retry_delay(response, 0) if response.status_code == 429 else None
    return GitLabError(code, message, response.status_code, retry_after)
