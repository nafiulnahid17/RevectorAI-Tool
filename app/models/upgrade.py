"""Backend response schemas exported for the future UI task."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    error_id: str
    error_code: str
    code: str
    category: str
    phase: str
    project_id: str | None = None
    job_id: str | None = None
    part_id: str | None = None
    part_type: str | None = None
    message: str
    technical_summary: str
    retryable: bool
    recoverable: bool
    suggested_actions: list[str] = Field(default_factory=list)
    timestamp: str


class JobResponse(BaseModel):
    job_id: str
    project_id: str
    part_id: str | None = None
    stage: str
    status: Literal["queued", "processing", "completed", "failed", "cancelled"]
    job_state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    created_at: str
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None
    finished_at: str | None = None
    error: ErrorEvent | None = None
    result: dict | None = None
    process_event: dict | None = None
