"""Safe normalized errors; deliberately exclude headers, environment and arbitrary metadata."""

import re
from uuid import uuid4

from app.errors.catalog import category, supported_actions
from app.models.project import now


def sanitize(value, secrets=()):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:api[_ -]?key|token|cookie|authorization|secret|password|credential|session[_ -]?signing[_ -]?key)\s*[=:]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    return text[:2000]


def normalize(
    code,
    message,
    retryable=True,
    *,
    phase="request",
    project_id=None,
    job_id=None,
    part_id=None,
    part_type=None,
    secrets=(),
):
    safe_message = sanitize(message, secrets)
    result = {
        "error_id": str(uuid4()),
        "error_code": code,
        "code": code,
        "category": category(code),
        "phase": phase,
        "project_id": project_id,
        "job_id": job_id,
        "part_id": part_id,
        "part_type": part_type,
        "message": safe_message,
        "technical_summary": safe_message,
        "retryable": retryable,
        "recoverable": retryable,
        "timestamp": now(),
    }
    result["suggested_actions"] = supported_actions(result)
    return result
