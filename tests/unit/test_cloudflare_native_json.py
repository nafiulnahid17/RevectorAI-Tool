"""Native request and validation guards; these use mocks, not live inference."""

import base64
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest

from app.ai.adapters import CloudflareProvider
from app.ai.router import AIRouter
from app.core.exceptions import EngineError
from app.errors.assistant import ErrorAssistant
from app.errors.normalization import normalize


MODEL = "@cf/meta/llama-4-scout-17b-16e-instruct"
BASE = "https://api.cloudflare.com/client/v4/accounts/" + "a" * 32


def router(respond):
    provider = CloudflareProvider(
        "cloudflare", BASE, "unit-private-token", MODEL,
        transport=httpx.MockTransport(respond),
    )
    return AIRouter(primary=None, fallback=provider)


def response(value):
    return httpx.Response(200, json={"success": True, "result": {"response": value}})


def test_native_analysis_sends_real_image_and_complete_schema_without_secret():
    def respond(request):
        body = json.loads(request.read())
        assert "unit-private-token" not in json.dumps(body)
        assert body["response_format"]["type"] == "json_schema"
        schema = body["response_format"]["json_schema"]
        assert set(schema["required"]) == set(schema["properties"])
        content = body["messages"][0]["content"]
        assert content[0]["type"] == "text"
        encoded = content[1]["image_url"]["url"].split(",", 1)[1]
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            assert image.getpixel((0, 0)) == (11, 22, 33)
        return response({"artwork_type": "jersey", "visible_parts": ["FRONT_BODY"]})

    value, metadata = router(respond).invoke(
        "analyze_artwork", Image.new("RGB", (64, 64), (11, 22, 33))
    )
    assert value["visible_parts"] == ["FRONT_BODY"]
    assert metadata["processing_mode"] == "fallback_ai"


def test_native_candidate_geometry_remains_strictly_validated():
    def respond(request):
        schema = json.loads(request.read())["response_format"]["json_schema"]
        assert schema["properties"]["candidates"]["maxItems"] == 32
        return response({"candidates": [{
            "part_type": "FRONT_BODY",
            "candidate_bbox": {"x": 0.8, "y": 0.1, "width": 0.5, "height": 0.5},
        }]})

    with pytest.raises(EngineError) as exc:
        router(respond).invoke("identify_parts", Image.new("RGB", (64, 64)))
    assert exc.value.code == "AI_PROVIDER_UNAVAILABLE"
    assert exc.value.diagnostics["attempts"][0]["code"] == "AI_RESPONSE_INVALID"


def test_native_box_representation_conversion_does_not_move_coordinates():
    def respond(request):
        return response({"candidates": [{
            "part_type": "LEFT_SLEEVE",
            "candidate_bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        }]})

    candidates, _ = router(respond).invoke("identify_parts", Image.new("RGB", (64, 64)))
    assert candidates[0]["candidate_bbox"] == (0.1, 0.2, 0.3, 0.4)


def test_native_advice_cannot_bypass_action_allowlist():
    def respond(request):
        schema = json.loads(request.read())["response_format"]["json_schema"]
        assert "delete_project" not in schema["properties"]["recommended_action"]["enum"]
        assert "known" not in schema["properties"]["cause_status"]["enum"]
        return response({
            "title": "Issue", "explanation": "Needs review", "likely_causes": [],
            "severity": "recoverable", "cause_status": "likely",
            "recommended_action": "delete_project", "secondary_actions": [],
            "user_message": "Review details",
        })

    advice = ErrorAssistant(router(respond)).explain(
        normalize("INVALID_PATH_GEOMETRY", "Invalid path", True, phase="vectorize")
    )
    assert advice["source"] == "deterministic_catalog"
    assert advice["ai_available"] is False


def test_legacy_cloudflare_request_contract_is_preserved():
    def respond(request):
        body = json.loads(request.read())
        assert body["prompt"] == "Return JSON"
        assert body["image"] and "messages" not in body
        return response('{"artwork_type":"jersey"}')

    provider = CloudflareProvider(
        "cloudflare", BASE, "unit-private-token", "@cf/meta/llama-3.2-11b-vision-instruct",
        transport=httpx.MockTransport(respond),
    )
    assert provider.text("Return JSON", Image.new("RGB", (64, 64)))["artwork_type"] == "jersey"
