"""Per-operation routing: only a successful validated response reports an active provider."""

import time

from PIL import Image

from app.ai.adapters import CloudflareProvider, CompatibleRESTProvider, GeminiProvider
from app.ai.contracts import ArtworkAnalysis, Candidate
from app.core.config import AISettings
from app.core.exceptions import EngineError


def make_provider(name, key, base, model, image_model, settings, transport=None):
    if not name:
        return None
    if not key and not model and not base and not image_model:
        return None
    if not key or not model:
        raise EngineError(
            "API_KEY_CONFIGURATION_ERROR",
            "Provider requires a server-configured credential and model",
        )
    defaults = {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
    }
    if name == "cloudflare":
        import re

        if not re.fullmatch(r"[a-fA-F0-9]{32}", settings.cloudflare_account_id):
            raise EngineError(
                "API_KEY_CONFIGURATION_ERROR", "Configure a valid Cloudflare account id"
            )
        base = (
            "https://api.cloudflare.com/client/v4/accounts/"
            + settings.cloudflare_account_id
        )
    elif name not in {*defaults, "custom", "compatible"}:
        raise EngineError(
            "AI_PROVIDER_UNSUPPORTED",
            "AI provider is not supported by the configured adapter registry",
        )
    cls = (
        CloudflareProvider
        if name == "cloudflare"
        else GeminiProvider
        if name == "gemini"
        else CompatibleRESTProvider
    )
    return cls(
        name,
        base or defaults.get(name, ""),
        key,
        model,
        image_model,
        settings.ai_timeout_seconds,
        transport,
    )


class AIRouter:
    def __init__(
        self,
        settings: AISettings | None = None,
        *,
        primary=None,
        fallback=None,
        error_mode=False,
    ):
        self.settings = settings or AISettings()
        self.primary, self.fallback = primary, fallback
        self.error_mode = error_mode
        self.configuration_errors = []
        if primary is None and fallback is None:
            s = self.settings
            name = s.error_ai_provider if error_mode else s.main_ai_provider
            secret = s.error_ai_api_key if error_mode else s.main_ai_api_key
            if name == "cloudflare" and error_mode:
                secret = secret or s.cloudflare_ai_token
            try:
                self.primary = make_provider(
                    name,
                    secret.get_secret_value() if secret else "",
                    s.error_ai_base_url if error_mode else s.main_ai_base_url,
                    (s.error_ai_model or s.cloudflare_ai_model)
                    if error_mode
                    else s.main_ai_model,
                    s.main_ai_image_model,
                    s,
                )
            except EngineError as exc:
                self.configuration_errors.append(exc.as_dict())
            if not (error_mode and name == "cloudflare"):
                try:
                    self.fallback = make_provider(
                        "cloudflare",
                        s.cloudflare_ai_token.get_secret_value()
                        if s.cloudflare_ai_token
                        else "",
                        "",
                        s.cloudflare_ai_model,
                        s.cloudflare_ai_image_model,
                        s,
                    )
                except EngineError as exc:
                    self.configuration_errors.append(exc.as_dict())

    def fingerprint(self) -> str:
        import hashlib
        import json

        identities = [
            (
                p.name,
                getattr(p, "base_url", None),
                getattr(p, "model", None),
                getattr(p, "image_model", None),
            )
            if p
            else None
            for p in (self.primary, self.fallback)
        ]
        return hashlib.sha256(json.dumps(identities).encode()).hexdigest()

    def configured(self) -> bool:
        return bool(self.primary or self.fallback)

    def invoke(self, operation: str, *args) -> tuple[object, dict]:
        started = time.perf_counter()
        attempts = list(self.configuration_errors)
        for mode, provider in [
            ("primary_ai", self.primary),
            ("fallback_ai", self.fallback),
        ]:
            if not provider:
                continue
            try:
                value = getattr(provider, operation)(*args)
                if operation == "analyze_artwork":
                    value = ArtworkAnalysis.model_validate(value).model_dump()
                elif operation == "identify_parts":
                    if not isinstance(value, list) or len(value) > 32:
                        raise ValueError("Invalid candidates")
                    value = [Candidate.model_validate(c).model_dump() for c in value]
                    for c in value:
                        x, y, w, h = c["candidate_bbox"]
                        if (
                            min(x, y) < 0
                            or min(w, h) <= 0
                            or x + w > 1.001
                            or y + h > 1.001
                        ):
                            raise ValueError("Invalid candidate coordinates")
                elif operation in {
                    "enhance_artwork",
                    "create_pattern_mockup",
                    "reconstruct_missing_part",
                } and (
                    not isinstance(value, Image.Image)
                    or min(value.size) < 64
                    or value.width * value.height > 8_000_000
                ):
                    raise ValueError("Invalid generated image")
                return value, {
                    "processing_mode": mode,
                    "provider": provider.name,
                    "model": getattr(
                        provider,
                        "image_model"
                        if operation
                        in {
                            "enhance_artwork",
                            "create_pattern_mockup",
                            "reconstruct_missing_part",
                        }
                        else "model",
                        None,
                    ),
                    "operation": operation,
                    "attempt_count": sum("provider" in a for a in attempts) + 1,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "failures": attempts,
                }
            except Exception as exc:  # noqa: BLE001 — provider boundary must route failures safely
                code = (
                    exc.code if isinstance(exc, EngineError) else "AI_RESPONSE_INVALID"
                )
                attempts.append({"provider": provider.name, "code": code})
        raise EngineError(
            "AI_PROVIDER_UNAVAILABLE",
            "No AI provider completed the request; review server-side provider configuration",
            diagnostics={"attempts": attempts},
        )
