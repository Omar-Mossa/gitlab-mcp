from __future__ import annotations

import httpx
import pytest
import respx

from gitlab_ci_mcp.errors import InputError
from gitlab_ci_mcp.models import ArtifactRead, DiagnoseOptions
from gitlab_ci_mcp.service import GitLabCIService


@respx.mock
async def test_failed_pipeline_diagnosis_collects_and_redacts_logs(
    ci_service: GitLabCIService,
) -> None:
    base = "https://gitlab.example.com/api/v4/projects/group%2Fproject"
    respx.get(f"{base}/pipelines/7").mock(
        return_value=httpx.Response(
            200, json={"id": 7, "status": "failed", "web_url": "https://gitlab.example.com/p/7"}
        )
    )
    respx.get(f"{base}/pipelines/7/jobs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 9,
                    "name": "test",
                    "stage": "test",
                    "status": "failed",
                    "failure_reason": "script_failure",
                },
                {"id": 8, "name": "build", "stage": "build", "status": "success"},
            ],
        )
    )
    respx.get(f"{base}/jobs/9/trace").mock(
        return_value=httpx.Response(200, text="starting\nPASSWORD=hunter2\nfailed")
    )
    respx.get(f"{base}/pipelines/7/bridges").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 11,
                    "name": "child",
                    "status": "failed",
                    "downstream_pipeline": {"id": 12, "status": "failed"},
                }
            ],
        )
    )

    diagnosis = await ci_service.diagnose_pipeline("group/project", 7, DiagnoseOptions())

    assert diagnosis["ok"] is True
    payload = diagnosis["data"]
    assert payload["job_counts"] == {"failed": 1, "success": 1}
    assert payload["bridges"][0]["downstream_pipeline"]["id"] == 12
    assert (
        payload["failed_or_canceled_jobs"][0]["log"]["text"]
        == "starting\nPASSWORD=[REDACTED]\nfailed"
    )


@respx.mock
async def test_inaccessible_job_log_is_reported_without_losing_other_diagnostics(
    ci_service: GitLabCIService,
) -> None:
    base = "https://gitlab.example.com/api/v4/projects/group%2Fproject"
    respx.get(f"{base}/pipelines/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "status": "failed"})
    )
    respx.get(f"{base}/pipelines/7/jobs").mock(
        return_value=httpx.Response(200, json=[{"id": 9, "name": "test", "status": "failed"}])
    )
    respx.get(f"{base}/jobs/9/trace").mock(return_value=httpx.Response(404))
    respx.get(f"{base}/pipelines/7/bridges").mock(return_value=httpx.Response(200, json=[]))

    diagnosis = await ci_service.diagnose_pipeline("group/project", 7, DiagnoseOptions())

    log_error = diagnosis["data"]["failed_or_canceled_jobs"][0]["log"]["error"]
    assert log_error["code"] == "not_found"


@respx.mock
async def test_cancel_pipeline_without_confirmation_never_mutates(
    ci_service: GitLabCIService,
) -> None:
    base = "https://gitlab.example.com/api/v4/projects/group%2Fproject"
    respx.get(f"{base}/pipelines/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "status": "running"})
    )
    cancel_route = respx.post(f"{base}/pipelines/7/cancel").mock(
        return_value=httpx.Response(200, json={"id": 7, "status": "canceled"})
    )

    preview = await ci_service.cancel_pipeline("group/project", 7, "")

    assert preview["confirmation_required"] == "CANCEL PIPELINE group/project#7"
    assert cancel_route.called is False


@respx.mock
async def test_cancel_pipeline_with_exact_confirmation_mutates(
    ci_service: GitLabCIService,
) -> None:
    base = "https://gitlab.example.com/api/v4/projects/group%2Fproject"
    respx.get(f"{base}/pipelines/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "status": "running"})
    )
    cancel_route = respx.post(f"{base}/pipelines/7/cancel").mock(
        return_value=httpx.Response(200, json={"id": 7, "status": "canceled"})
    )

    response = await ci_service.cancel_pipeline(
        "group/project", 7, "CANCEL PIPELINE group/project#7"
    )

    assert response["data"]["status"] == "canceled"
    assert cancel_route.called is True


@respx.mock
async def test_canceling_finished_pipeline_is_rejected_before_mutation(
    ci_service: GitLabCIService,
) -> None:
    base = "https://gitlab.example.com/api/v4/projects/group%2Fproject"
    respx.get(f"{base}/pipelines/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "status": "success"})
    )

    with pytest.raises(InputError, match="pipeline is success"):
        await ci_service.cancel_pipeline("group/project", 7, "CANCEL PIPELINE group/project#7")


@respx.mock
async def test_binary_artifact_is_rejected(ci_service: GitLabCIService) -> None:
    url = "https://gitlab.example.com/api/v4/projects/group%2Fproject/jobs/9/artifacts/out.bin"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"binary\x00payload"))

    with pytest.raises(InputError, match="binary"):
        await ci_service.read_artifact_text(
            "group/project", 9, ArtifactRead(artifact_path="out.bin")
        )
