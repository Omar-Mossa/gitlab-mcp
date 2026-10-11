from __future__ import annotations

from typing import Any
from urllib.parse import quote

from gitlab_ci_mcp.client import GitLabClient, GitLabRequest
from gitlab_ci_mcp.config import Settings
from gitlab_ci_mcp.errors import GitLabError, InputError
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
from gitlab_ci_mcp.security import (
    LogBounds,
    Redactor,
    bounded_log,
    decoded_text,
    validated_artifact_path,
)

ACTIVE_STATES = {"created", "pending", "preparing", "running", "waiting_for_resource"}
RETRYABLE_STATES = {"failed", "canceled"}


class GitLabCIService:
    def __init__(self, client: GitLabClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._redactor = Redactor(settings.redact_patterns)

    async def list_pipelines(self, project: str, filters: PipelineFilters) -> dict[str, Any]:
        params = filters.model_dump(exclude_none=True)
        path = self._project_api(project, "pipelines")
        pipelines = await self._client.json_object_list(GitLabRequest("GET", path, params))
        return self._success(f"Found {len(pipelines)} pipeline(s)", pipelines)

    async def get_pipeline(
        self, project: str, pipeline_id: int, options: PipelineOptions
    ) -> dict[str, Any]:
        pipeline = await self._pipeline(project, pipeline_id)
        if options.include_jobs:
            pipeline["jobs"] = await self._pipeline_jobs(project, pipeline_id)
        if options.include_test_summary:
            path = self._project_api(project, f"pipelines/{pipeline_id}/test_report_summary")
            pipeline["test_report_summary"] = await self._client.json_object(
                GitLabRequest("GET", path)
            )
        return self._success(
            f"Pipeline {pipeline_id}: {pipeline.get('status', 'unknown')}", pipeline
        )

    async def diagnose_pipeline(
        self, project: str, pipeline_id: int | None, options: DiagnoseOptions
    ) -> dict[str, Any]:
        selected_id = pipeline_id or await self._latest_pipeline_id(project, options.ref)
        pipeline = await self._pipeline(project, selected_id)
        jobs = await self._pipeline_jobs(project, selected_id)
        bridges = await self._pipeline_bridges(project, selected_id)
        relevant_jobs = [job for job in jobs if job.get("status") in RETRYABLE_STATES]
        job_diagnostics = [
            await self._job_diagnostic(project, job, options.log_tail_lines)
            for job in relevant_jobs
        ]
        pipeline_status = pipeline.get("status", "unknown")
        summary = (
            f"Pipeline {selected_id}: {pipeline_status}; "
            f"{len(relevant_jobs)} failed/canceled job(s)"
        )
        return self._success(summary, _diagnosis_payload(pipeline, jobs, bridges, job_diagnostics))

    async def get_job(self, project: str, job_id: int) -> dict[str, Any]:
        job = await self._job(project, job_id)
        return self._success(f"Job {job_id}: {job.get('status', 'unknown')}", job)

    async def get_job_log(
        self, project: str, job_id: int, options: JobLogOptions
    ) -> dict[str, Any]:
        line_limit = min(options.tail_lines, self._settings.max_log_lines)
        path = self._project_api(project, f"jobs/{job_id}/trace")
        raw_log = await self._client.text(GitLabRequest("GET", path))
        log = bounded_log(
            raw_log,
            self._redactor,
            LogBounds(line_limit, self._settings.max_response_bytes, options.search_pattern),
        )
        return self._success(f"Log for job {job_id}", log)

    async def list_job_artifacts(self, project: str, job_id: int) -> dict[str, Any]:
        job = await self._job(project, job_id)
        artifacts = job.get("artifacts", [])
        if not isinstance(artifacts, list) or any(
            not isinstance(artifact, dict) for artifact in artifacts
        ):
            raise GitLabError("invalid_response", "GitLab job artifacts were not an object list")
        payload = {"job_id": job_id, "artifacts": artifacts, "web_url": job.get("web_url")}
        return self._success(f"Found {len(artifacts)} artifact(s) for job {job_id}", payload)

    async def read_artifact_text(
        self, project: str, job_id: int, request: ArtifactRead
    ) -> dict[str, Any]:
        artifact_path = validated_artifact_path(request.artifact_path)
        byte_limit = min(request.max_bytes, self._settings.max_artifact_bytes)
        encoded_path = quote(artifact_path, safe="/")
        path = self._project_api(project, f"jobs/{job_id}/artifacts/{encoded_path}")
        raw_content = await self._client.raw_bytes(GitLabRequest("GET", path), byte_limit)
        text = self._redactor.clean(decoded_text(raw_content))
        payload = {
            "job_id": job_id,
            "artifact_path": artifact_path,
            "text": text,
            "redaction": "best_effort",
        }
        return self._success(f"Read {len(raw_content)} byte(s) from {artifact_path}", payload)

    async def lint_ci_config(self, project: str, request: LintRequest) -> dict[str, Any]:
        path = self._project_api(project, "ci/lint")
        if request.content is not None:
            lint_response = await self._lint_supplied_content(path, request)
        else:
            lint_response = await self._lint_existing_content(path, request)
        validity = "valid" if lint_response.get("valid") else "invalid"
        return self._success(f"CI configuration is {validity}", lint_response)

    async def run_pipeline(
        self, project: str, request: RunPipelineRequest, confirmation: str
    ) -> dict[str, Any]:
        await self._client.json_object(GitLabRequest("GET", self._project_api(project, "")))
        expected = f"RUN {project}@{request.ref}"
        preview = self._confirmation_preview("run_pipeline", expected, confirmation)
        if preview:
            return preview
        if request.inputs:
            await self._client.ensure_version("pipeline inputs", (17, 10))
        body = request.model_dump(exclude_none=True, exclude={"ref"})
        path = self._project_api(project, "pipeline")
        pipeline = await self._client.json_object(
            GitLabRequest("POST", path, {"ref": request.ref}, body)
        )
        return self._success("Pipeline started", _without_variable_values(pipeline))

    async def retry_pipeline(
        self, project: str, pipeline_id: int, confirmation: str
    ) -> dict[str, Any]:
        jobs = await self._pipeline_jobs(project, pipeline_id)
        if not any(job.get("status") in RETRYABLE_STATES for job in jobs):
            raise InputError("pipeline has no failed or canceled jobs to retry")
        expected = f"RETRY PIPELINE {project}#{pipeline_id}"
        preview = self._confirmation_preview("retry_pipeline", expected, confirmation)
        if preview:
            return preview
        path = self._project_api(project, f"pipelines/{pipeline_id}/retry")
        pipeline = await self._client.json_object(GitLabRequest("POST", path))
        return self._success(f"Pipeline {pipeline_id} retry requested", pipeline)

    async def cancel_pipeline(
        self, project: str, pipeline_id: int, confirmation: str
    ) -> dict[str, Any]:
        pipeline = await self._pipeline(project, pipeline_id)
        _require_state("pipeline", str(pipeline.get("status")), ACTIVE_STATES)
        expected = f"CANCEL PIPELINE {project}#{pipeline_id}"
        preview = self._confirmation_preview("cancel_pipeline", expected, confirmation)
        if preview:
            return preview
        path = self._project_api(project, f"pipelines/{pipeline_id}/cancel")
        canceled = await self._client.json_object(GitLabRequest("POST", path))
        return self._success(f"Pipeline {pipeline_id} cancel requested", canceled)

    async def retry_job(self, project: str, job_id: int, confirmation: str) -> dict[str, Any]:
        job = await self._job(project, job_id)
        _require_state("job", str(job.get("status")), RETRYABLE_STATES)
        expected = f"RETRY JOB {project}#{job_id}"
        preview = self._confirmation_preview("retry_job", expected, confirmation)
        if preview:
            return preview
        path = self._project_api(project, f"jobs/{job_id}/retry")
        retried = await self._client.json_object(GitLabRequest("POST", path))
        return self._success(f"Job {job_id} retry requested", retried)

    async def cancel_job(
        self, project: str, request: CancelJobRequest, confirmation: str
    ) -> dict[str, Any]:
        job = await self._job(project, request.job_id)
        allowed_states = ACTIVE_STATES | ({"canceling"} if request.force else set())
        _require_state("job", str(job.get("status")), allowed_states)
        expected = f"CANCEL JOB {project}#{request.job_id}"
        preview = self._confirmation_preview("cancel_job", expected, confirmation)
        if preview:
            return preview
        path = self._project_api(project, f"jobs/{request.job_id}/cancel")
        canceled = await self._client.json_object(
            GitLabRequest("POST", path, {"force": request.force})
        )
        return self._success(f"Job {request.job_id} cancel requested", canceled)

    async def play_manual_job(
        self, project: str, request: PlayJobRequest, confirmation: str
    ) -> dict[str, Any]:
        job = await self._job(project, request.job_id)
        _require_state("job", str(job.get("status")), {"manual"})
        expected = f"PLAY JOB {project}#{request.job_id}"
        preview = self._confirmation_preview("play_manual_job", expected, confirmation)
        if preview:
            return preview
        variables = [
            {"key": variable.key, "value": variable.value} for variable in request.variables or []
        ]
        body = {"job_variables_attributes": variables} if variables else None
        path = self._project_api(project, f"jobs/{request.job_id}/play")
        played = await self._client.json_object(GitLabRequest("POST", path, json_body=body))
        return self._success(
            f"Manual job {request.job_id} started", _without_variable_values(played)
        )

    async def _pipeline(self, project: str, pipeline_id: int) -> dict[str, Any]:
        path = self._project_api(project, f"pipelines/{pipeline_id}")
        return await self._client.json_object(GitLabRequest("GET", path))

    async def _pipeline_jobs(self, project: str, pipeline_id: int) -> list[dict[str, Any]]:
        path = self._project_api(project, f"pipelines/{pipeline_id}/jobs")
        params = {"per_page": 100}
        return await self._client.paginated_json(GitLabRequest("GET", path, params))

    async def _pipeline_bridges(self, project: str, pipeline_id: int) -> list[dict[str, Any]]:
        path = self._project_api(project, f"pipelines/{pipeline_id}/bridges")
        return await self._client.paginated_json(GitLabRequest("GET", path, {"per_page": 100}))

    async def _job(self, project: str, job_id: int) -> dict[str, Any]:
        path = self._project_api(project, f"jobs/{job_id}")
        return await self._client.json_object(GitLabRequest("GET", path))

    async def _latest_pipeline_id(self, project: str, ref: str | None) -> int:
        params: dict[str, Any] = {"per_page": 1, "order_by": "id", "sort": "desc"}
        if ref:
            params["ref"] = ref
        path = self._project_api(project, "pipelines")
        pipelines = await self._client.json_object_list(GitLabRequest("GET", path, params))
        if not pipelines:
            raise InputError("no pipelines matched the requested project and ref")
        return int(pipelines[0]["id"])

    async def _job_diagnostic(
        self, project: str, job: dict[str, Any], line_limit: int
    ) -> dict[str, Any]:
        job_id = int(job["id"])
        log = await self._diagnostic_log(project, job_id, line_limit)
        return _job_summary(job, log)

    async def _diagnostic_log(
        self, project: str, job_id: int, line_limit: int
    ) -> dict[str, object]:
        path = self._project_api(project, f"jobs/{job_id}/trace")
        try:
            raw_log = await self._client.text(GitLabRequest("GET", path))
            return bounded_log(
                raw_log,
                self._redactor,
                LogBounds(line_limit, self._settings.max_response_bytes),
            )
        except GitLabError as exc:
            if exc.code not in {"forbidden", "not_found"}:
                raise
            return {"error": exc.as_dict()}

    async def _lint_supplied_content(self, path: str, request: LintRequest) -> dict[str, Any]:
        body: dict[str, Any] = {"content": request.content, "include_jobs": request.include_jobs}
        if request.dry_run_ref:
            body.update({"dry_run": True, "ref": request.dry_run_ref})
        return await self._client.json_object(GitLabRequest("POST", path, json_body=body))

    async def _lint_existing_content(self, path: str, request: LintRequest) -> dict[str, Any]:
        params: dict[str, Any] = {"include_jobs": request.include_jobs}
        if request.content_ref:
            params["content_ref"] = request.content_ref
        if request.dry_run_ref:
            params.update({"dry_run": True, "dry_run_ref": request.dry_run_ref})
        return await self._client.json_object(GitLabRequest("GET", path, params))

    @staticmethod
    def _confirmation_preview(action: str, expected: str, supplied: str) -> dict[str, Any] | None:
        if supplied == expected:
            return None
        return {
            "ok": False,
            "summary": "Confirmation required; no mutation was performed",
            "confirmation_required": expected,
            "action": action,
        }

    @staticmethod
    def _success(summary: str, payload: Any) -> dict[str, Any]:
        return {"ok": True, "summary": summary, "data": payload}

    def _project_api(self, project: str, suffix: str) -> str:
        base = f"projects/{self._client.project_path(project)}"
        return f"{base}/{suffix}" if suffix else base


def _job_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _bridge_summary(bridge: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": bridge.get("id"),
        "name": bridge.get("name"),
        "status": bridge.get("status"),
        "web_url": bridge.get("web_url"),
        "downstream_pipeline": bridge.get("downstream_pipeline"),
    }


def _diagnosis_payload(
    pipeline: dict[str, Any],
    jobs: list[dict[str, Any]],
    bridges: list[dict[str, Any]],
    job_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "pipeline": pipeline,
        "failed_or_canceled_jobs": job_diagnostics,
        "job_counts": _job_counts(jobs),
        "bridges": [_bridge_summary(bridge) for bridge in bridges],
        "redaction": "best_effort",
    }


def _job_summary(job: dict[str, Any], log: dict[str, object]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "name": job.get("name"),
        "stage": job.get("stage"),
        "status": job.get("status"),
        "failure_reason": job.get("failure_reason"),
        "duration": job.get("duration"),
        "runner": job.get("runner"),
        "web_url": job.get("web_url"),
        "log": log,
    }


def _require_state(subject: str, current: str, allowed: set[str]) -> None:
    if current not in allowed:
        expected = ", ".join(sorted(allowed))
        raise InputError(f"{subject} is {current}; expected one of: {expected}")


def _without_variable_values(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    sanitized = dict(payload)
    sanitized.pop("variables", None)
    sanitized.pop("inputs", None)
    return sanitized
