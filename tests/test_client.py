from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from gitlab_ci_mcp.client import GitLabClient, GitLabRequest
from gitlab_ci_mcp.errors import GitLabError, ResponseTooLargeError, UnsafeRedirectError


@respx.mock
async def test_authentication_error_is_sanitized(
    gitlab_client: GitLabClient,
) -> None:
    respx.get("https://gitlab.example.com/api/v4/user").mock(
        return_value=httpx.Response(401, json={"message": "token test-token rejected"})
    )

    with pytest.raises(GitLabError) as raised:
        await gitlab_client.json(GitLabRequest("GET", "user"))

    assert raised.value.code == "unauthorized"
    assert "test-token" not in str(raised.value)


@pytest.mark.parametrize(
    "redirect_target",
    ["https://objects.example.net/file", "http://gitlab.example.com/insecure"],
)
@respx.mock
async def test_cross_origin_redirect_is_rejected(
    gitlab_client: GitLabClient, redirect_target: str
) -> None:
    respx.get("https://gitlab.example.com/api/v4/redirect").mock(
        return_value=httpx.Response(302, headers={"location": redirect_target})
    )

    with pytest.raises(UnsafeRedirectError):
        await gitlab_client.raw_bytes(GitLabRequest("GET", "redirect"), 100)


@respx.mock
async def test_rate_limit_is_retried_with_bounded_delay(gitlab_client: GitLabClient) -> None:
    route = respx.get("https://gitlab.example.com/api/v4/projects")
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "60"}),
        httpx.Response(200, json=[]),
    ]

    with patch("gitlab_ci_mcp.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
        pipelines = await gitlab_client.json(GitLabRequest("GET", "projects"))

    assert pipelines == []
    assert route.call_count == 2
    sleep.assert_awaited_once_with(30.0)


@respx.mock
async def test_response_larger_than_limit_is_rejected(gitlab_client: GitLabClient) -> None:
    respx.get("https://gitlab.example.com/api/v4/large").mock(
        return_value=httpx.Response(200, content=b"12345")
    )

    with pytest.raises(ResponseTooLargeError):
        await gitlab_client.raw_bytes(GitLabRequest("GET", "large"), 4)


@respx.mock
async def test_paginated_request_collects_pages_until_short_page(
    gitlab_client: GitLabClient,
) -> None:
    route = respx.get("https://gitlab.example.com/api/v4/jobs")
    route.side_effect = [
        httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
        httpx.Response(200, json=[{"id": 3}]),
    ]

    jobs = await gitlab_client.paginated_json(GitLabRequest("GET", "jobs", {"per_page": 2}))

    assert jobs == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [call.request.url.params["page"] for call in route.calls] == ["1", "2"]
