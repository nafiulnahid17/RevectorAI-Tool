"""Advisory only: provider output has no authority to mutate files or validate vectors."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.errors.catalog import ACTION_ALLOWLIST, local_help
from app.errors.normalization import sanitize


class Advice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=120)
    explanation: str = Field(max_length=2000)
    likely_causes: list[str] = Field(default_factory=list, max_length=5)
    severity: Literal["recoverable", "fatal", "unknown"] = "unknown"
    cause_status: Literal["known", "likely", "unknown"] = "unknown"
    recommended_action: str
    secondary_actions: list[str] = Field(default_factory=list, max_length=8)
    user_message: str = Field(max_length=2000)


class ErrorAssistant:
    def __init__(self, router, secrets=()):
        self.router, self.secrets = router, secrets

    def explain(
        self, error: dict, question: str = "", history: list[dict] | None = None
    ) -> dict:
        local = local_help(error)
        # Explicit data projection; never pass a project dump or raw exception object.
        context = {
            k: sanitize(error[k], self.secrets)
            for k in (
                "error_code",
                "category",
                "phase",
                "message",
                "part_type",
                "job_state",
                "provider",
            )
            if error.get(k) is not None
        }
        report = error.get("validation") or {}
        context["validation"] = {
            k: report[k]
            for k in (
                "valid_svg",
                "true_vector",
                "embedded_rasters",
                "path_count",
                "illustrator_compatibility",
                "render_succeeded",
            )
            if k in report
        }
        context["validation"]["errors"] = [
            sanitize(v, self.secrets) for v in report.get("errors", [])[:8]
        ]
        context["retry_history"] = [
            {"action": v.get("action"), "outcome": v.get("outcome")}
            for v in error.get("feedback", [])[-5:]
        ]
        context["supported_actions"] = local["supported_actions"]
        context["completed_part_types"] = [
            sanitize(v, self.secrets)
            for v in error.get("completed_part_types", [])[:32]
        ]
        context["question"] = sanitize(question, self.secrets)
        context["history"] = [
            {"role": h["role"], "message": sanitize(h["message"], self.secrets)}
            for h in (history or [])[-6:]
        ]
        try:
            value, metadata = self.router.invoke("explain_error", context)
            advice = Advice.model_validate(value).model_dump()
            actions = [advice["recommended_action"], *advice["secondary_actions"]]
            if any(
                a not in ACTION_ALLOWLIST or a not in local["supported_actions"]
                for a in actions
            ):
                raise ValueError("Unsupported assistant action")
            text = " ".join(
                str(advice[k]) for k in ("title", "explanation", "user_message")
            )
            if re.search(
                r"(?i)(?:validation|validator)\s+(?:has\s+)?(?:passed|succeeded)|(?:error|issue)\s+(?:is\s+)?(?:fixed|resolved)|fully\s+editable|true\s+vector\s+ready",
                text,
            ):
                raise ValueError(
                    "Assistant cannot declare validation or repair success"
                )
            # Cause assertions from an LLM are hypotheses; known causes come from diagnostics.
            if advice["cause_status"] == "known":
                advice["cause_status"] = "likely"
            for k in ("title", "explanation", "user_message"):
                advice[k] = sanitize(advice[k], self.secrets)
            advice["likely_causes"] = [
                sanitize(v, self.secrets) for v in advice["likely_causes"]
            ]
            return {
                **advice,
                "source": "ai",
                "provider": metadata["provider"],
                "processing_mode": metadata["processing_mode"],
                "supported_actions": local["supported_actions"],
            }
        except Exception:  # noqa: BLE001 — advisory inference must not break processing
            return {**local, "ai_available": False}
