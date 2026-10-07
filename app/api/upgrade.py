"""Owner-checked orchestration, review and advisory assistant API."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from app.ai.contracts import SlotType
from app.api.projects import public_project
from app.api.schemas import StageRequest, StrictModel
from app.core.exceptions import EngineError
from app.core.security import project_access
from app.errors.assistant import ErrorAssistant
from app.errors.normalization import normalize, sanitize
from app.models.project import now
from app.models.upgrade import JobResponse

router = APIRouter(
    prefix="/api/revector", tags=["AI-assisted production and error recovery"]
)


class RecoveryRequest(StageRequest):
    fallback_trace: bool = False


class MissingRequest(StageRequest):
    slot: SlotType


class SlotDecision(StrictModel):
    status: Literal["confirmed", "blank"]
    part_id: str | None = None


class ReviewRequest(StageRequest):
    decisions: dict[SlotType, SlotDecision] = Field(default_factory=dict)


class SlotRequest(StageRequest):
    slot: SlotType
    status: Literal["blank", "missing"]


class ErrorContext(StrictModel):
    error_code: str = Field("UNKNOWN_ERROR", max_length=100, pattern=r"^[A-Z0-9_]+$")
    message: str = Field("Unknown error", max_length=2000)
    phase: str = Field("request", max_length=80)
    part_id: str | None = Field(None, max_length=100)
    retryable: bool = True


class AssistantRequest(StrictModel):
    project_id: str | None = None
    error_id: str | None = None
    error: ErrorContext | None = None
    question: str = Field("", max_length=1000)


class FeedbackRequest(StageRequest):
    error_id: str
    action: str
    outcome: Literal["SUCCESS", "FAILED", "CANCELLED"]


def submit(stage, body, request):
    project_access(request, body.project_id)
    return request.app.state.queue.submit(
        body.project_id,
        stage,
        body.model_dump(exclude={"project_id"}, exclude_none=True),
    )


@router.get("/capabilities/ai")
def ai_capabilities(request: Request):
    e = request.app.state.engine
    quota = e.ai_router.quota_status()
    primary_configured = bool(
        getattr(e.ai_router, "_dynamic_openrouter", False)
        or e.ai_router.primary is not None
    )
    return {
        "primary_configured": primary_configured,
        "fallback_configured": e.ai_router.fallback_configured(),
        "main_ai": {"configured": primary_configured},
        "fallback_ai": {
            "configured": e.ai_router.fallback_configured(),
            "quota": quota,
        },
        "error_assistant_configured": e.error_ai_router.configured(),
        "provider_profile": (
            "openrouter-one-key"
            if getattr(e.ai_router, "_dynamic_openrouter", False)
            else "legacy"
        ),
        "operation_models": e.ai_router.operation_models(),
        "connection_verified": False,
        "configuration_errors": [
            value["code"] for value in e.ai_router.configuration_errors
        ],
        "native_ai_export": False,
        "note": "Configuration availability is not a successful provider request.",
    }

@router.get("/error-catalog")
def error_catalog():
    from app.errors.catalog import ACTION_ALLOWLIST, CATALOG

    return {
        "version": "1.0",
        "source": "deterministic_catalog",
        "categories": {
            name: {"title": entry[0], "explanation": entry[1], "actions": entry[2]}
            for name, entry in CATALOG.items()
        },
        "allowlisted_actions": sorted(ACTION_ALLOWLIST),
    }


@router.post("/prepare", status_code=202, response_model=JobResponse)
def prepare(body: StageRequest, request: Request):
    return submit("prepare", body, request)


@router.post("/production", status_code=202, response_model=JobResponse)
def production(body: RecoveryRequest, request: Request):
    return submit("production", body, request)


@router.post("/recover-part", status_code=202, response_model=JobResponse)
def recover(body: RecoveryRequest, request: Request):
    if not body.part_id:
        raise EngineError("PART_NOT_FOUND", "Choose the failed part to retry")
    return submit("recover-part", body, request)


@router.post("/ai-missing", status_code=202, response_model=JobResponse)
def missing(body: MissingRequest, request: Request):
    return submit("ai-missing", body, request)


@router.post("/review/confirm", status_code=202, response_model=JobResponse)
def review(body: ReviewRequest, request: Request):
    project_access(request, body.project_id)
    request.app.state.queue.assert_idle(body.project_id)
    selected = body.part_ids or []
    request.app.state.engine.workflow.review(
        body.project_id,
        {k: v.model_dump() for k, v in body.decisions.items()},
        selected,
    )
    return request.app.state.queue.submit(
        body.project_id,
        "production",
        {"part_ids": selected} if selected else {},
    )


@router.post("/slots/update")
def update_slot(body: SlotRequest, request: Request):
    p = project_access(request, body.project_id)
    request.app.state.queue.assert_idle(body.project_id)
    engine = request.app.state.engine
    with engine.storage.lock(body.project_id):
        p = engine.load(body.project_id)
        if any(a.type == body.slot.lower() for a in p.parts):
            raise EngineError(
                "PART_ALREADY_EXISTS",
                "Remove the existing component before leaving its slot blank",
            )
        p.slots[body.slot].status = body.status
        p.slots[body.slot].part_id = None
        p.ai_metadata.pop("review", None)
        engine.invalidate(p, "compose")
        engine.storage.save(p)
    return public_project(p)


@router.get("/projects/{project_id}/events")
def events(project_id: str, request: Request):
    return project_access(request, project_id).events


@router.get("/projects/{project_id}/errors")
def errors(project_id: str, request: Request):
    return project_access(request, project_id).error_history


@router.post("/assistant/explain")
def explain(body: AssistantRequest, request: Request):
    e = request.app.state.engine
    p = project_access(request, body.project_id) if body.project_id else None
    from app.errors.service import credentials

    secrets = credentials(e)
    error = (
        next((v for v in p.error_history if v["error_id"] == body.error_id), None)
        if p and body.error_id
        else None
    )
    if error is None:
        context = body.error or ErrorContext()
        part = (
            next((v for v in p.parts if v.part_id == context.part_id), None)
            if p
            else None
        )
        error = normalize(
            context.error_code,
            context.message,
            context.retryable,
            phase=context.phase,
            project_id=body.project_id,
            part_id=part.part_id if part else None,
            part_type=part.type if part else None,
            secrets=secrets,
        )
    if p:
        error = {
            **error,
            "validation": p.validation or {},
            "job_state": str(p.state),
            "completed_part_types": [
                a.type
                for a in p.parts
                if a.vector and a.cache.get("optimize") and not a.error
            ],
        }
    session = p.assistant_sessions.get(error["error_id"], []) if p else []
    advice = ErrorAssistant(e.error_ai_router, secrets).explain(
        error, body.question, session
    )
    memory_persisted = False
    if p:
        try:
            with e.storage.lock(p.project_id):
                p = e.load(p.project_id)
                if not any(
                    v.get("error_id") == error["error_id"] for v in p.error_history
                ):
                    p.error_history.append(
                        {
                            k: v
                            for k, v in error.items()
                            if k not in {"validation", "completed_part_types"}
                        }
                    )
                    p.error_history = p.error_history[-50:]
                session.extend(
                    [
                        {"role": "user", "message": sanitize(body.question, secrets)},
                        {"role": "assistant", "message": advice["user_message"]},
                    ]
                )
                p.assistant_sessions[error["error_id"]] = session[-6:]
                if len(p.assistant_sessions) > 10:
                    del p.assistant_sessions[next(iter(p.assistant_sessions))]
                p.events.append(
                    {
                        "event": "ERROR_ASSISTANT_READY",
                        "error_id": error["error_id"],
                        "timestamp": now(),
                        "source": advice["source"],
                    }
                )
                p.events = p.events[-300:]
                e.storage.save(p)
                memory_persisted = True
        except EngineError as exc:
            if exc.code != "PROJECT_BUSY":
                raise
            # Advisory help remains available while a pipeline stage owns the
            # project lock. Never overwrite its in-flight production manifest.
    return {
        "error_id": error["error_id"],
        "advice": advice,
        "memory_persisted": memory_persisted,
    }


@router.post("/assistant/feedback")
def feedback(body: FeedbackRequest, request: Request):
    e = request.app.state.engine
    p = project_access(request, body.project_id)
    from app.errors.catalog import supported_actions

    error = next((v for v in p.error_history if v["error_id"] == body.error_id), None)
    if not error or body.action not in supported_actions(error):
        raise EngineError(
            "UNSUPPORTED_RECOVERY_ACTION",
            "Recovery action is not supported for this error",
            status=422,
        )
    with e.storage.lock(p.project_id):
        p = e.load(p.project_id)
        error = next(v for v in p.error_history if v["error_id"] == body.error_id)
        error.setdefault("feedback", []).append(
            {
                "action": body.action,
                "outcome": body.outcome,
                "observed_by": "client",
                "timestamp": now(),
            }
        )
        from app.core.logging import log_event

        log_event(
            "recovery_feedback",
            error_id=body.error_id,
            project_id=p.project_id,
            phase=error["phase"],
            error_code=error["error_code"],
            resolution=body.outcome,
            action=body.action,
            timestamp=now(),
        )
        error["feedback"] = error["feedback"][-20:]
        e.storage.save(p)
    return {"recorded": True, "outcome": body.outcome}
