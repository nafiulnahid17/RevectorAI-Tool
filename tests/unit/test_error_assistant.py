import pytest
from app.ai.router import AIRouter
from app.errors.assistant import ErrorAssistant
from app.errors.normalization import normalize
from app.errors.catalog import ACTION_ALLOWLIST, local_help


class AssistantProvider:
    name = "mock"

    def explain_error(self, context):
        self.context = context
        return {
            "title": "Geometry issue",
            "explanation": "Likely malformed trace.",
            "likely_causes": ["Malformed path"],
            "severity": "recoverable",
            "cause_status": "likely",
            "recommended_action": "use_fallback_trace",
            "secondary_actions": ["view_error_details"],
            "user_message": "Retry only the failed part.",
        }


def test_safe_context_excludes_secrets_and_arbitrary_metadata():
    p = AssistantProvider()
    assistant = ErrorAssistant(AIRouter(primary=p), ["test-private-value"])
    error = normalize(
        "INVALID_PATH_GEOMETRY",
        "token=test-private-value invalid",
        phase="vectorize",
        part_id="part_one",
    )
    error.update(
        authorization="Bearer secret",
        cookies="session-secret",
        environment={"secret": "value"},
    )
    result = assistant.explain(
        error,
        "API_KEY=test-private-value",
        [{"role": "user", "message": "Bearer test-private-value"}],
    )
    assert result["source"] == "ai"
    assert "test-private-value" not in str(p.context)
    assert not {"authorization", "cookies", "environment"} & set(p.context)
    assert result["recommended_action"] in ACTION_ALLOWLIST


def test_unknown_model_action_rejected_without_execution():
    p = AssistantProvider()
    original = p.explain_error
    p.explain_error = lambda c: {**original(c), "recommended_action": "delete_project"}
    error = normalize(
        "INVALID_PATH_GEOMETRY", "invalid", phase="vectorize", part_id="part_one"
    )
    result = ErrorAssistant(AIRouter(primary=p)).explain(error)
    assert result["source"] == "deterministic_catalog"
    assert "delete_project" not in result["supported_actions"]


@pytest.mark.parametrize(
    "code",
    [
        "NETWORK_ERROR",
        "INTERNET_CONNECTION_ERROR",
        "REQUEST_TIMEOUT",
        "RAILWAY_ENGINE_UNAVAILABLE",
        "ENGINE_ERROR",
        "VECTOR_TRACE_FAILED",
        "INVALID_PATH_GEOMETRY",
        "GEOMETRY_ERROR",
        "DETECTION_ERROR",
        "PARTS_ERROR",
        "MISSING_PART",
        "MOCKUP_GENERATION_ERROR",
        "AI_PROVIDER_ERROR",
        "AI_TIMEOUT",
        "CLOUDFLARE_AI_ERROR",
        "AUTH_ERROR",
        "PERMISSION_ERROR",
        "API_KEY_CONFIGURATION_ERROR",
        "STORAGE_ERROR",
        "UPLOAD_ERROR",
        "FILE_FORMAT_ERROR",
        "FILE_TOO_LARGE",
        "JOB_ERROR",
        "JOB_TIMEOUT",
        "JOB_CANCEL_ERROR",
        "VALIDATION_ERROR",
        "EMBEDDED_RASTER_ERROR",
        "ILLUSTRATOR_COMPATIBILITY_ERROR",
        "EXPORT_ERROR",
        "PRODUCTION_PACK_ERROR",
        "UNKNOWN_ERROR",
    ],
)
def test_every_category_has_safe_local_guidance(code):
    error = normalize(code, "safe message", phase="production")
    advice = local_help(error)
    assert (
        advice["explanation"] and set(advice["supported_actions"]) <= ACTION_ALLOWLIST
    )
    assert advice["source"] == "deterministic_catalog"


def test_ai_cannot_claim_repair_or_validation_success():
    provider = AssistantProvider()
    original = provider.explain_error
    provider.explain_error = lambda context: {
        **original(context),
        "user_message": "Validation passed. Your error is fixed.",
    }
    error = normalize(
        "INVALID_PATH_GEOMETRY", "invalid", phase="vectorize", part_id="part_one"
    )
    advice = ErrorAssistant(AIRouter(primary=provider)).explain(error)
    assert advice["source"] == "deterministic_catalog"
