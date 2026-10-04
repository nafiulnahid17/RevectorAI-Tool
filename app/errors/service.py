"""Persist safe error events without demoting valid artwork for unrelated API errors."""

from app.core.logging import log_event
from app.errors.normalization import normalize
from app.models.project import now


def credentials(engine):
    settings = engine.ai_router.settings
    errors = engine.error_ai_router.settings
    return [
        value.get_secret_value()
        for value in (
            engine.settings.api_key,
            settings.main_ai_api_key,
            settings.cloudflare_ai_token,
            errors.error_ai_api_key,
            errors.cloudflare_ai_token,
        )
        if value
    ]


def record(engine, error):
    pid = error.get("project_id")
    if pid:
        with engine.storage.lock(pid):
            p = engine.load(pid)
            if not any(v.get("error_id") == error["error_id"] for v in p.error_history):
                p.error_history.append(error)
                p.error_history = p.error_history[-50:]
            p.error = error
            p.events.append(
                {
                    "event": "ERROR_OCCURRED",
                    "error_id": error["error_id"],
                    "error_code": error["error_code"],
                    "phase": error["phase"],
                    "part_id": error.get("part_id"),
                    "timestamp": now(),
                }
            )
            p.events = p.events[-300:]
            engine.storage.save(p)
    log_event(
        "error",
        error_id=error["error_id"],
        project_id=pid,
        job_id=error.get("job_id"),
        part_id=error.get("part_id"),
        phase=error["phase"],
        error_code=error["error_code"],
        timestamp=error["timestamp"],
    )


def request_error(request, code, message, retryable=True):
    engine = getattr(request.app.state, "engine", None)
    error = normalize(
        code,
        message,
        retryable,
        phase=request.url.path.rsplit("/", 1)[-1],
        project_id=getattr(request.state, "project_id", None),
        secrets=credentials(engine) if engine else (),
    )
    try:
        record(engine, error) if engine else None
    except Exception:  # noqa: BLE001 — secondary error logging cannot replace the original
        # Logging a storage/lock error must never replace the actual client error.
        log_event(
            "error_record_unavailable", error_id=error["error_id"], error_code=code
        )
    return error
