"""One-key OpenRouter routing, fallback quota and canonical mockup contract."""

from io import BytesIO
import base64
import json

import httpx
import pytest
from PIL import Image

from app.ai.adapters import OpenRouterProvider, sanitize_structured_schema
from app.ai.contracts import ArtworkAnalysis
from app.ai.image_quality import preset_target_dimensions
from app.ai.prompts import MASTER_MOCKUP_COMMAND, MOCKUP_VERSION
from app.ai.quota import FallbackQuota
from app.ai.router import AIRouter
from app.core.config import AISettings
from app.core.exceptions import EngineError


class GoodProvider:
    name = "test"
    model = "test"
    image_model = ""

    def analyze_artwork(self, image):
        return {"artwork_type": "jersey"}


class FailedProvider(GoodProvider):
    def analyze_artwork(self, image):
        raise EngineError("AI_TIMEOUT", "timeout")


class FlakyStructuredProvider(GoodProvider):
    def __init__(self):
        self.calls = 0

    def analyze_artwork(self, image):
        self.calls += 1
        if self.calls == 1:
            raise EngineError("AI_RESPONSE_INVALID", "invalid structured response")
        return {"artwork_type": "jersey"}


def test_one_key_profile_resolves_all_operation_models():
    router = AIRouter(AISettings(openrouter_api_key="sk-or-test"))
    models = router.operation_models()
    assert models["analyze_artwork"]["primary"] == "openai/gpt-5.6-terra"
    assert models["identify_parts"]["primary"] == "openai/gpt-5.6-terra"
    assert models["verify_pattern_mockup"]["primary"] == "openai/gpt-5.6-terra"
    assert models["explain_error"]["primary"] == "openai/gpt-5.6-luna"
    assert models["enhance_artwork"]["primary"] == "openai/gpt-image-2.5-flare"
    assert models["create_pattern_mockup"]["primary"] == "openai/gpt-image-2.5-flare"
    assert models["reconstruct_missing_part"]["primary"] == "openai/gpt-image-2.5-sunburst"
    assert models["analyze_artwork"]["fallback"] == "google/gemini-3.8-flash"
    assert models["identify_parts"]["fallback"] == "google/gemini-3.8-flash"
    assert models["create_pattern_mockup"]["fallback"] == "google/gemini-3-pro-image"
    assert models["reconstruct_missing_part"]["fallback"] == "google/gemini-3-pro-image"
    assert router.configured()
    assert router.fallback_configured() is True


def test_operation_override_does_not_change_other_routes():
    settings = AISettings(
        openrouter_api_key="sk-or-test",
        analyze_model="vendor/custom-analyzer",
    )
    models = AIRouter(settings).operation_models()
    assert models["analyze_artwork"]["primary"] == "vendor/custom-analyzer"
    assert models["identify_parts"]["primary"] == "openai/gpt-5.6-terra"


def test_openrouter_uses_dedicated_image_api_with_reference():
    image = Image.new("RGB", (1024, 768), "black")
    stream = BytesIO()
    image.save(stream, format="PNG")

    def respond(request):
        assert request.url.path.endswith("/images")
        payload = json.loads(request.content)
        assert payload["model"] == "openai/gpt-image-2.5-flare"
        assert payload["aspect_ratio"] == "4:3"
        assert payload["output_format"] == "png"
        assert payload["resolution"] == "2K"
        assert payload["input_references"][0]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {"b64_json": base64.b64encode(stream.getvalue()).decode()}
                ],
                "usage": {"cost": 0.01},
            },
        )

    provider = OpenRouterProvider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "sk-or-test",
        "openai/gpt-image-2.5-flare",
        "openai/gpt-image-2.5-flare",
        transport=httpx.MockTransport(respond),
    )
    result = provider.create_pattern_mockup(image, "command", (1440, 1080))
    assert result.size == (1024, 768)
    assert provider.last_usage["cost"] == 0.01


def test_global_fallback_quota_is_rolling_and_persistent(tmp_path):
    now = [1_000_000.0]
    path = tmp_path / "quota.sqlite3"
    quota = FallbackQuota(path, 2, 24, clock=lambda: now[0])
    assert quota.reserve()["remaining"] == 1
    assert quota.reserve()["remaining"] == 0
    with pytest.raises(EngineError) as exc:
        quota.reserve()
    assert exc.value.code == "FALLBACK_DAILY_LIMIT_REACHED"

    restarted = FallbackQuota(path, 2, 24, clock=lambda: now[0])
    assert restarted.status()["used"] == 2
    now[0] += 24 * 3600 + 1
    assert restarted.status()["used"] == 0


def test_transient_structured_primary_failure_retries_before_fallback(tmp_path):
    quota = FallbackQuota(tmp_path / "quota.sqlite3", 2, 24)
    primary = FlakyStructuredProvider()
    router = AIRouter(
        primary=primary,
        fallback=GoodProvider(),
        quota=quota,
    )

    value, metadata = router.invoke(
        "analyze_artwork", Image.new("RGB", (64, 64))
    )

    assert value["artwork_type"] == "jersey"
    assert primary.calls == 2
    assert metadata["processing_mode"] == "primary_ai"
    assert metadata["attempt_count"] == 2
    assert metadata["failures"][0]["code"] == "AI_RESPONSE_INVALID"
    assert metadata["failures"][0]["retry_scheduled"] is True
    assert quota.status()["used"] == 0


def test_exhausted_fallback_is_recoverable_provider_error(tmp_path):
    quota = FallbackQuota(tmp_path / "quota.sqlite3", 0, 24)
    router = AIRouter(
        primary=FailedProvider(),
        fallback=GoodProvider(),
        quota=quota,
    )
    with pytest.raises(EngineError) as exc:
        router.invoke("analyze_artwork", Image.new("RGB", (64, 64)))
    assert exc.value.code == "AI_PROVIDER_UNAVAILABLE"
    assert exc.value.status == 503
    assert exc.value.recoverable is True
    assert exc.value.diagnostics["fallback_quota"]["remaining"] == 0
    assert any(
        attempt.get("code") == "FALLBACK_DAILY_LIMIT_REACHED"
        and attempt.get("dispatched") is False
        for attempt in exc.value.diagnostics["attempts"]
    )


def test_dispatched_failed_fallback_still_consumes_quota(tmp_path):
    quota = FallbackQuota(tmp_path / "quota.sqlite3", 2, 24)
    router = AIRouter(
        primary=FailedProvider(),
        fallback=FailedProvider(),
        quota=quota,
    )
    with pytest.raises(EngineError):
        router.invoke("analyze_artwork", Image.new("RGB", (64, 64)))
    assert quota.status()["used"] == 1


def test_master_mockup_command_is_dynamic_and_versioned():
    assert MOCKUP_VERSION == "jersey-dynamic-production-layout/2.0"
    assert "Do NOT force an eight-part template" in MASTER_MOCKUP_COMMAND
    assert "If 2, 8, 12, 16 or more real components are present" in MASTER_MOCKUP_COMMAND
    assert "Do not invent missing" in MASTER_MOCKUP_COMMAND


def test_workspace_presets_control_ai_raster_targets():
    assert preset_target_dimensions("BALANCED", "4:3") == (960, 720)
    assert preset_target_dimensions("FAST", "4:3") == (1440, 1080)
    assert preset_target_dimensions("ULTRA", "4:3") == (1920, 1440)
    assert preset_target_dimensions("BALANCED", "1:1") == (720, 720)
    assert preset_target_dimensions("FAST", "1:1") == (1080, 1080)
    assert preset_target_dimensions("ULTRA", "1:1") == (1440, 1440)


def test_artwork_analysis_schema_has_no_open_ended_objects():
    schema = sanitize_structured_schema(ArtworkAnalysis.model_json_schema())
    schema["required"] = list(schema.get("properties", {}))
    schema["additionalProperties"] = False

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                if "properties" in value:
                    assert set(value.get("required", [])) == set(value["properties"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(schema)
    assert schema["properties"]["logos"]["items"]["$ref"] == "#/$defs/LogoEvidence"
    assert schema["properties"]["text_regions"]["items"]["$ref"] == "#/$defs/TextRegionEvidence"


def test_openrouter_analyze_artwork_sends_strict_nested_schema():
    def respond(request):
        payload = json.loads(request.content)
        js = payload["response_format"]["json_schema"]
        schema = js["schema"]
        assert js["strict"] is True
        assert schema["additionalProperties"] is False
        assert schema["$defs"]["LogoEvidence"]["additionalProperties"] is False
        assert schema["$defs"]["TextRegionEvidence"]["additionalProperties"] is False
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "artwork_type": "jersey",
                                    "expected_parts": [],
                                    "visible_parts": [],
                                    "missing_parts": [],
                                    "uncertain_parts": [],
                                    "dominant_colors": [],
                                    "logos": [],
                                    "text_regions": [],
                                    "names": [],
                                    "numbers": [],
                                    "sponsors": [],
                                    "patterns": [],
                                    "collar_design": "",
                                    "sleeve_design": "",
                                    "confidence": None,
                                    "notes": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = OpenRouterProvider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "sk-or-test",
        "openai/gpt-5.6-terra",
        "",
        transport=httpx.MockTransport(respond),
    )
    result = provider.analyze_artwork(Image.new("RGB", (64, 64), "black"))
    assert result["artwork_type"] == "jersey"


def test_openrouter_structured_text_uses_json_schema():
    def respond(request):
        payload = json.loads(request.content)
        assert request.url.path.endswith("/chat/completions")
        assert payload["model"] == "openai/gpt-5.6-terra"
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["provider"]["require_parameters"] is True
        assert payload["provider"]["allow_fallbacks"] is True
        assert payload["provider"]["sort"] == "latency"
        assert payload["max_completion_tokens"] == 2500
        assert "max_tokens" not in payload
        assert "temperature" not in payload
        assert "reasoning" not in payload
        assert "plugins" not in payload
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"value": "ok"})
                        }
                    }
                ],
                "usage": {"total_tokens": 3},
            },
        )

    provider = OpenRouterProvider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "sk-or-test",
        "openai/gpt-5.6-terra",
        "",
        transport=httpx.MockTransport(respond),
    )
    result = provider.structured_text(
        "Return the value.",
        None,
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        schema_name="test_schema",
    )
    assert result == {"value": "ok"}
    assert provider.last_usage["total_tokens"] == 3


def test_openrouter_gemini_structured_text_uses_supported_completion_fields():
    def respond(request):
        payload = json.loads(request.content)
        assert payload["model"] == "google/gemini-3.8-flash"
        assert payload["max_tokens"] == 2500
        assert payload["temperature"] == 0
        assert "max_completion_tokens" not in payload
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"value": "ok"})}}
                ],
                "usage": {"total_tokens": 3},
            },
        )

    provider = OpenRouterProvider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "sk-or-test",
        "google/gemini-3.8-flash",
        "",
        transport=httpx.MockTransport(respond),
    )
    result = provider.structured_text(
        "Return the value.",
        None,
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        schema_name="test_schema",
    )
    assert result == {"value": "ok"}


def test_dynamic_openrouter_fallback_ignores_legacy_local_quota(tmp_path):
    quota = FallbackQuota(tmp_path / "quota.sqlite3", 0, 24)

    def respond(request):
        payload = json.loads(request.content)
        if payload["model"] == "openai/gpt-5.6-terra":
            return httpx.Response(503, json={"error": {"message": "primary unavailable"}})
        assert payload["model"] == "google/gemini-3.8-flash"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"artwork_type": "jersey"})
                        }
                    }
                ]
            },
        )

    router = AIRouter(
        AISettings(
            openrouter_api_key="sk-or-test",
            analyze_model="openai/gpt-5.6-terra",
            analyze_fallback_model="google/gemini-3.8-flash",
        ),
        quota=quota,
        transport=httpx.MockTransport(respond),
    )
    value, metadata = router.invoke(
        "analyze_artwork", Image.new("RGB", (64, 64), "black")
    )
    assert value["artwork_type"] == "jersey"
    assert metadata["processing_mode"] == "fallback_ai"
    assert metadata["model"] == "google/gemini-3.8-flash"
    assert metadata["fallback_quota"] is None
    assert quota.status()["used"] == 0


def test_gemini_schema_sanitizer_drops_unsupported_pydantic_keywords():
    schema = {
        "type": "object",
        "properties": {
            "confidence": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "default": None,
                "minimum": 0,
                "maximum": 1,
            },
            "notes": {
                "type": "string",
                "default": "",
                "maxLength": 2000,
            },
            "box": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1,
            },
        },
        "required": ["confidence", "notes", "box"],
        "additionalProperties": False,
    }
    cleaned = sanitize_structured_schema(schema)
    assert "default" not in cleaned["properties"]["confidence"]
    assert "default" not in cleaned["properties"]["notes"]
    assert "maxLength" not in cleaned["properties"]["notes"]
    assert "exclusiveMinimum" not in cleaned["properties"]["box"]
    assert cleaned["properties"]["confidence"]["minimum"] == 0
    assert cleaned["properties"]["confidence"]["maximum"] == 1
    assert cleaned["additionalProperties"] is False
