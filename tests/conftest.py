from __future__ import annotations

import httpx
import pytest

from gitlab_ci_mcp.client import GitLabClient
from gitlab_ci_mcp.config import Settings
from gitlab_ci_mcp.service import GitLabCIService


@pytest.fixture
def settings() -> Settings:
    return Settings(gitlab_url="https://gitlab.example.com", token="test-token")


@pytest.fixture
async def http_client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield client


@pytest.fixture
def gitlab_client(settings: Settings, http_client: httpx.AsyncClient) -> GitLabClient:
    return GitLabClient(settings, http_client)


@pytest.fixture
def ci_service(gitlab_client: GitLabClient, settings: Settings) -> GitLabCIService:
    return GitLabCIService(gitlab_client, settings)
