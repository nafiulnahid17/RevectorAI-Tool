"""One-key OpenRouter routing, fallback quota and canonical mockup contract."""

from io import BytesIO
import base64
import json

import httpx
import pytest
from PIL import Image

from app.ai.adapters import OpenRouterProvider
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


def test_one_key_profile_resolves_all_operation_models():
    router = AIRouter(AISettings(openrouter_api_key="sk-or-test"))
    models = router.operation_models()
    assert models["analyze_artwork"]["primary"] == "openai/gpt-6-astra"
    assert models["create_pattern_mockup"]["primary"] == "openai/gpt-image-2.5-sunburst"
    assert models["verify_pattern_mockup"]["primary"] == "google/gemini-3.1-pro-preview"
    assert models["explain_error"]["primary"] == "google/gemini-3.8-flash"
    assert router.configured() and router.fallback_configured()


def test_operation_override_does_not_change_other_routes():
    settings = AISettings(
        openrouter_api_key="sk-or-test",
        analyze_model="vendor/custom-analyzer",
    )
    models = AIRouter(settings).operation_models()
    assert models["analyze_artwork"]["primary"] == "vendor/custom-analyzer"
    assert models["identify_parts"]["primary"] == "openai/gpt-6-astra"


def test_openrouter_uses_dedicated_image_api_with_reference():
    image = Image.new("RGB", (1024, 768), "black")
    stream = BytesIO()
    image.save(stream, format="PNG")

    def respond(request):
        assert request.url.path.endswith("/images")
        payload = json.loads(request.content)
        assert payload["model"] == "openai/gpt-image-2.5-sunburst"
        assert payload["aspect_ratio"] == "4:3"
        assert payload["resolution"] == "1K"
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
        "openai/gpt-image-2.5-sunburst",
        "openai/gpt-image-2.5-sunburst",
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


def test_primary_failure_plus_exhausted_fallback_hard_locks(tmp_path):
    quota = FallbackQuota(tmp_path / "quota.sqlite3", 0, 24)
    router = AIRouter(
        primary=FailedProvider(),
        fallback=GoodProvider(),
        quota=quota,
    )
    with pytest.raises(EngineError) as exc:
        router.invoke("analyze_artwork", Image.new("RGB", (64, 64)))
    assert exc.value.code == "TOOL_LOCKED_AI_UNAVAILABLE"
    assert exc.value.diagnostics["fallback_quota"]["remaining"] == 0


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


def test_master_mockup_command_is_versioned_and_canonical():
    assert MOCKUP_VERSION == "jersey-production-layout/2.0"
    assert "CUT THE COLLAR COMPLETELY OUT OF BOTH BODY PANELS." in MASTER_MOCKUP_COMMAND
    assert "TOTAL = 8 SEPARATED COMPONENTS." in MASTER_MOCKUP_COMMAND
    assert "uploaded Original Image and its Enhanced Image" in MASTER_MOCKUP_COMMAND
