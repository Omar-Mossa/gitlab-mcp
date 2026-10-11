from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec

import httpx
from mcp.server.mcpserver import Context, MCPServer

from gitlab_ci_mcp.client import GitLabClient
from gitlab_ci_mcp.config import Settings
from gitlab_ci_mcp.errors import GitLabError
from gitlab_ci_mcp.models import (
    ArtifactRead,
    CancelJobRequest,
    DiagnoseOptions,
    JobLogOptions,
    LintRequest,
    PipelineFilters,
    PipelineOptions,
    PlayJobRequest,
    RunPipelineRequest,
)
from gitlab_ci_mcp.service import GitLabCIService

ToolParameters = ParamSpec("ToolParameters")


@dataclass(slots=True)
class AppContext:
    ci: GitLabCIService


def _structured_errors(
    tool: Callable[ToolParameters, Awaitable[dict[str, Any]]],
) -> Callable[ToolParameters, Awaitable[dict[str, Any]]]:
    @wraps(tool)
    async def wrapped(
        *args: ToolParameters.args, **kwargs: ToolParameters.kwargs
    ) -> dict[str, Any]:
        try:
            return await tool(*args, **kwargs)
        except GitLabError as exc:
            return {"ok": False, "error": exc.as_dict()}

    return wrapped


def create_server(settings: Settings) -> MCPServer[AppContext]:
    @asynccontextmanager
    async def lifespan(_: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        timeout = httpx.Timeout(settings.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http_client:
            client = GitLabClient(settings, http_client)
            yield AppContext(ci=GitLabCIService(client, settings))

    server = MCPServer[AppContext](
        "gitlab-ci-mcp",
        description="Inspect, diagnose, and safely operate GitLab CI pipelines.",
        version="0.1.0",
        lifespan=lifespan,
    )
    _register_read_tools(server)
    if settings.enable_writes:
        _register_write_tools(server)
    return server


def _register_read_tools(server: MCPServer[AppContext]) -> None:
    for tool in READ_TOOLS:
        server.add_tool(tool)


def _register_write_tools(server: MCPServer[AppContext]) -> None:
    for tool in WRITE_TOOLS:
        server.add_tool(tool)


def _service(ctx: Context[AppContext]) -> GitLabCIService:
    return ctx.request_context.lifespan_context.ci


@_structured_errors
async def list_pipelines(
    project: str, filters: PipelineFilters | None = None, *, ctx: Context[AppContext]
) -> dict[str, Any]:
    """List GitLab CI pipelines visible to the configured token."""
    return await _service(ctx).list_pipelines(project, filters or PipelineFilters())


@_structured_errors
async def get_pipeline(
    project: str,
    pipeline_id: int,
    options: PipelineOptions | None = None,
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Get a pipeline with optional jobs and test report summary."""
    return await _service(ctx).get_pipeline(project, pipeline_id, options or PipelineOptions())


@_structured_errors
async def diagnose_pipeline(
    project: str,
    pipeline_id: int | None = None,
    options: DiagnoseOptions | None = None,
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Gather deterministic failure context and bounded, redacted job logs."""
    return await _service(ctx).diagnose_pipeline(project, pipeline_id, options or DiagnoseOptions())


@_structured_errors
async def get_job(project: str, job_id: int, *, ctx: Context[AppContext]) -> dict[str, Any]:
    """Get one GitLab CI job."""
    return await _service(ctx).get_job(project, job_id)


@_structured_errors
async def get_job_log(
    project: str,
    job_id: int,
    options: JobLogOptions | None = None,
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Read a bounded job log with ANSI escapes and likely secrets removed."""
    return await _service(ctx).get_job_log(project, job_id, options or JobLogOptions())


@_structured_errors
async def list_job_artifacts(
    project: str, job_id: int, *, ctx: Context[AppContext]
) -> dict[str, Any]:
    """List artifact metadata reported for a job."""
    return await _service(ctx).list_job_artifacts(project, job_id)


@_structured_errors
async def read_artifact_text(
    project: str,
    job_id: int,
    request: ArtifactRead,
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Read one bounded UTF-8 text file from a job artifact archive."""
    return await _service(ctx).read_artifact_text(project, job_id, request)


@_structured_errors
async def lint_ci_config(
    project: str, request: LintRequest | None = None, *, ctx: Context[AppContext]
) -> dict[str, Any]:
    """Validate supplied or repository GitLab CI configuration."""
    return await _service(ctx).lint_ci_config(project, request or LintRequest())


@_structured_errors
async def run_pipeline(
    project: str,
    request: RunPipelineRequest,
    confirmation: str = "",
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Run a pipeline after returning and receiving an exact confirmation phrase."""
    return await _service(ctx).run_pipeline(project, request, confirmation)


@_structured_errors
async def retry_pipeline(
    project: str,
    pipeline_id: int,
    confirmation: str = "",
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Retry failed or canceled pipeline jobs after exact confirmation."""
    return await _service(ctx).retry_pipeline(project, pipeline_id, confirmation)


@_structured_errors
async def cancel_pipeline(
    project: str,
    pipeline_id: int,
    confirmation: str = "",
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Cancel an active pipeline after exact confirmation."""
    return await _service(ctx).cancel_pipeline(project, pipeline_id, confirmation)


@_structured_errors
async def retry_job(
    project: str,
    job_id: int,
    confirmation: str = "",
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Retry a failed or canceled job after exact confirmation."""
    return await _service(ctx).retry_job(project, job_id, confirmation)


@_structured_errors
async def cancel_job(
    project: str,
    request: CancelJobRequest,
    confirmation: str = "",
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Cancel an active job after exact confirmation."""
    return await _service(ctx).cancel_job(project, request, confirmation)


@_structured_errors
async def play_manual_job(
    project: str,
    request: PlayJobRequest,
    confirmation: str = "",
    *,
    ctx: Context[AppContext],
) -> dict[str, Any]:
    """Start a manual job after exact confirmation."""
    return await _service(ctx).play_manual_job(project, request, confirmation)


READ_TOOLS = (
    list_pipelines,
    get_pipeline,
    diagnose_pipeline,
    get_job,
    get_job_log,
    list_job_artifacts,
    read_artifact_text,
    lint_ci_config,
)

WRITE_TOOLS = (
    run_pipeline,
    retry_pipeline,
    cancel_pipeline,
    retry_job,
    cancel_job,
    play_manual_job,
)


def main() -> None:
    settings = Settings.from_env()
    create_server(settings).run(transport="stdio")
