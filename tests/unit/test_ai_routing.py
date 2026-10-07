"""HTTP contracts and routing without billing a real provider or faking connection status."""

from io import BytesIO
import base64
import httpx
import pytest
from PIL import Image
from app.ai.adapters import CompatibleRESTProvider, CloudflareProvider, GeminiProvider
from app.ai.router import AIRouter
from app.core.config import AISettings
from app.core.exceptions import EngineError


class Provider:
    name = "test-provider"

    def analyze_artwork(self, image):
        return {"artwork_type": "jersey"}


class FailedProvider(Provider):
    def analyze_artwork(self, image):
        raise EngineError("AI_TIMEOUT", "timed out")


@pytest.mark.parametrize(
    "primary,expected",
    [
        (None, "fallback_ai"),
        (Provider(), "primary_ai"),
        (FailedProvider(), "fallback_ai"),
    ],
)
def test_actual_primary_or_fallback(primary, expected):
    router = AIRouter(primary=primary, fallback=Provider())
    value, metadata = router.invoke("analyze_artwork", Image.new("RGB", (64, 64)))
    assert metadata["processing_mode"] == expected
    assert value["confidence"] is None


def test_both_unavailable_returns_structured_failure():
    with pytest.raises(EngineError) as result:
        AIRouter(primary=FailedProvider(), fallback=FailedProvider()).invoke(
            "analyze_artwork", Image.new("RGB", (64, 64))
        )
    # analyze_artwork retries the primary once before the fallback attempt.
    assert len(result.value.diagnostics["attempts"]) == 3
    assert result.value.code == "AI_PROVIDER_UNAVAILABLE"


def test_main_blank_real_cloudflare_text_contract():
    s = AISettings(
        main_ai_provider="",
        cloudflare_account_id="a" * 32,
        cloudflare_ai_token="test-token",
        cloudflare_ai_model="@cf/meta/test",
    )
    router = AIRouter(s)

    def respond(request):
        assert request.headers["authorization"] == "Bearer test-token"
        assert "/ai/run/@cf/meta/test" in str(request.url)
        return httpx.Response(
            200,
            json={"success": True, "result": {"response": '{"artwork_type":"jersey"}'}},
        )

    router.fallback.transport = httpx.MockTransport(respond)
    _, metadata = router.invoke("analyze_artwork", Image.new("RGB", (64, 64)))
    assert (
        metadata["processing_mode"] == "fallback_ai"
        and metadata["provider"] == "cloudflare"
    )


def test_cloudflare_real_reference_image_edit_contract():
    img = Image.new("RGB", (1024, 768))
    stream = BytesIO()
    img.save(stream, format="PNG")

    def respond(request):
        data = request.read()
        assert b'name="input_image_0"' in data and b'name="prompt"' in data
        assert request.headers["content-type"].startswith(
            "multipart/form-data; boundary="
        )
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {"image": base64.b64encode(stream.getvalue()).decode()},
            },
        )

    p = CloudflareProvider(
        "cloudflare",
        "https://api.cloudflare.com/client/v4/accounts/" + "a" * 32,
        "test-token",
        "vision",
        "@cf/black-forest-labs/flux-2-klein-4b",
        transport=httpx.MockTransport(respond),
    )
    assert p.enhance_artwork(img, (1024, 768)).size == (1024, 768)
    assert p.create_pattern_mockup(img, "command", (1024, 768)).size == (1024, 768)


def test_compatible_text_and_image_edit():
    img = Image.new("RGB", (64, 64))
    stream = BytesIO()
    img.save(stream, format="PNG")

    def respond(request):
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"artwork_type":"jersey"}'}}]
                },
            )
        assert (
            request.url.path.endswith("/images/edits")
            and b'name="image"' in request.read()
        )
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(stream.getvalue()).decode()}]},
        )

    p = CompatibleRESTProvider(
        "custom",
        "https://provider.example/v1",
        "test-token",
        "vision",
        "image",
        transport=httpx.MockTransport(respond),
    )
    assert p.analyze_artwork(img)["artwork_type"] == "jersey"
    assert p.enhance_artwork(img, (1024, 768)).size == (64, 64)


def test_gemini_uses_secret_header_not_url():
    def respond(request):
        assert request.headers["x-goog-api-key"] == "test-token"
        assert not request.url.query and "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": '{"artwork_type":"jersey"}'}]}}
                ]
            },
        )

    p = GeminiProvider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        "test-token",
        "vision",
        transport=httpx.MockTransport(respond),
    )
    assert p.analyze_artwork(Image.new("RGB", (64, 64)))["artwork_type"] == "jersey"


@pytest.mark.parametrize(
    "bbox", [[0, 0, -0.1, 0.2], [0.9, 0.1, 0.2, 0.2], [float("nan"), 0, 0.2, 0.2]]
)
def test_invalid_candidate_cannot_be_evidence(bbox):
    p = Provider()
    p.identify_parts = lambda image: [
        {"part_type": "LEFT_SLEEVE", "candidate_bbox": bbox}
    ]
    with pytest.raises(EngineError):
        AIRouter(primary=p).invoke("identify_parts", Image.new("RGB", (64, 64)))


def test_primary_invalid_schema_uses_actual_fallback():
    broken = Provider()
    broken.analyze_artwork = lambda image: {"confidence": 4}
    value, metadata = AIRouter(primary=broken, fallback=Provider()).invoke(
        "analyze_artwork", Image.new("RGB", (64, 64))
    )
    assert metadata["processing_mode"] == "fallback_ai"
    assert metadata["failures"][0]["code"] == "AI_RESPONSE_INVALID"


def test_cf_failure_never_reports_fallback_as_active():
    def respond(request):
        return httpx.Response(403, json={"error": "do-not-echo-provider-body"})

    provider = CloudflareProvider(
        "cloudflare",
        "https://api.cloudflare.com/client/v4/accounts/" + "a" * 32,
        "test-token",
        "vision",
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(EngineError) as failure:
        AIRouter(fallback=provider).invoke(
            "analyze_artwork", Image.new("RGB", (64, 64))
        )
    assert "do-not-echo-provider-body" not in failure.value.message
    assert failure.value.diagnostics["attempts"][0]["code"] == "AI_PROVIDER_ERROR"


def test_partial_cf_configuration_is_reported_honestly():
    router = AIRouter(
        AISettings(
            cloudflare_ai_model="vision",
            cloudflare_ai_token="test-token",
            cloudflare_account_id="invalid",
        )
    )
    assert not router.configured() and router.configuration_errors
    with pytest.raises(EngineError):
        router.invoke("analyze_artwork", Image.new("RGB", (64, 64)))


def test_provider_change_changes_cache_fingerprint():
    p = Provider()
    router = AIRouter(primary=p)
    old = router.fingerprint()
    p.model = "another-model"
    assert old != router.fingerprint()


def test_dynamic_candidate_accepts_nonstandard_real_component():
    from app.ai.contracts import Candidate

    candidate = Candidate.model_validate(
        {
            "part_type": "SIDE_PANEL_02",
            "candidate_bbox": [0.1, 0.2, 0.3, 0.4],
            "confidence": 0.9,
            "uncertain": False,
            "notes": "Visible detached side panel",
        }
    )
    assert candidate.part_type == "SIDE_PANEL_02"
