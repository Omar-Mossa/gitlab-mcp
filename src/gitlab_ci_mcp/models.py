from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PipelineFilters(StrictModel):
    ref: str | None = None
    status: str | None = None
    source: str | None = None
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class PipelineOptions(StrictModel):
    include_jobs: bool = True
    include_test_summary: bool = False


class DiagnoseOptions(StrictModel):
    ref: str | None = None
    log_tail_lines: int = Field(default=200, ge=1, le=1000)


class JobLogOptions(StrictModel):
    tail_lines: int = Field(default=300, ge=1, le=1000)
    search_pattern: str | None = Field(default=None, max_length=200)


class ArtifactRead(StrictModel):
    artifact_path: str
    max_bytes: int = Field(default=100_000, ge=1, le=1_000_000)


class LintRequest(StrictModel):
    content: str | None = None
    content_ref: str | None = None
    dry_run_ref: str | None = None
    include_jobs: bool = True

    @model_validator(mode="after")
    def references_match_mode(self) -> LintRequest:
        if self.content is not None and self.content_ref is not None:
            raise ValueError("content_ref cannot be used when content is supplied")
        return self


class PipelineVariable(StrictModel):
    key: str = Field(min_length=1, max_length=255)
    value: str
    variable_type: Literal["env_var", "file"] = "env_var"


class RunPipelineRequest(StrictModel):
    ref: str = Field(min_length=1)
    inputs: dict[str, Any] | None = None
    variables: list[PipelineVariable] | None = None


class CancelJobRequest(StrictModel):
    job_id: int = Field(gt=0)
    force: bool = False


class PlayJobRequest(StrictModel):
    job_id: int = Field(gt=0)
    variables: list[PipelineVariable] | None = None
