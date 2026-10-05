"""Operation-specific AI routing with one-key OpenRouter production defaults."""

import hashlib
import json
import time

from PIL import Image

from app.ai.adapters import (
    CloudflareProvider,
    CompatibleRESTProvider,
    GeminiProvider,
    OpenRouterProvider,
)
from app.ai.contracts import ArtworkAnalysis, Candidate, MockupQC
from app.ai.model_profile import (
    OPERATIONS,
    is_image_operation,
    resolve_model,
    resolved_profile,
)
from app.core.config import AISettings
from app.core.exceptions import EngineError


PRIMARY_RETRY_OPERATIONS = frozenset(
    {"analyze_artwork", "identify_parts", "verify_pattern_mockup", "explain_error"}
)
PRIMARY_RETRYABLE_CODES = frozenset(
    {"AI_RESPONSE_INVALID", "AI_PROVIDER_ERROR", "AI_TIMEOUT"}
)


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
        else OpenRouterProvider
        if name == "openrouter"
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
        quota=None,
        transport=None,
    ):
        self.settings = settings or AISettings()
        self.primary, self.fallback = primary, fallback
        self.error_mode = error_mode
        self.quota = quota
        self.transport = transport
        self.configuration_errors = []
        self._dynamic_cache = {}
        self._dynamic_openrouter = bool(
            primary is None
            and fallback is None
            and self.settings.openrouter_api_key
            and self.settings.openrouter_api_key.get_secret_value()
        )
        if self._dynamic_openrouter:
            return

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
                    transport,
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
                        transport,
                    )
                except EngineError as exc:
                    self.configuration_errors.append(exc.as_dict())

    def _provider_for(self, operation: str, fallback: bool):
        if not self._dynamic_openrouter:
            return self.fallback if fallback else self.primary
        key = (operation, fallback)
        if key in self._dynamic_cache:
            return self._dynamic_cache[key]
        model = resolve_model(self.settings, operation, fallback)
        if not model:
            return None
        secret = self.settings.openrouter_api_key
        provider = make_provider(
            "openrouter",
            secret.get_secret_value() if secret else "",
            self.settings.openrouter_base_url,
            model,
            model if is_image_operation(operation) else "",
            self.settings,
            self.transport,
        )
        self._dynamic_cache[key] = provider
        return provider

    def operation_models(self) -> dict:
        if self._dynamic_openrouter:
            return resolved_profile(self.settings)
        result = {}
        for operation in OPERATIONS:
            primary = self.primary
            fallback = self.fallback
            result[operation] = {
                "primary": (
                    getattr(primary, "image_model", "")
                    if is_image_operation(operation)
                    else getattr(primary, "model", "")
                )
                if primary
                else "",
                "fallback": (
                    getattr(fallback, "image_model", "")
                    if is_image_operation(operation)
                    else getattr(fallback, "model", "")
                )
                if fallback
                else "",
            }
        return result

    def fingerprint(self) -> str:
        if self._dynamic_openrouter:
            identities = {
                "provider": "openrouter",
                "base_url": self.settings.openrouter_base_url,
                "profile": self.operation_models(),
            }
        else:
            identities = [
                (
                    provider.name,
                    getattr(provider, "base_url", None),
                    getattr(provider, "model", None),
                    getattr(provider, "image_model", None),
                )
                if provider
                else None
                for provider in (self.primary, self.fallback)
            ]
        return hashlib.sha256(
            json.dumps(identities, sort_keys=True).encode()
        ).hexdigest()

    def configured(self) -> bool:
        return bool(self._dynamic_openrouter or self.primary or self.fallback)

    def fallback_configured(self) -> bool:
        if self._dynamic_openrouter:
            return any(resolve_model(self.settings, op, True) for op in OPERATIONS)
        return self.fallback is not None

    def supports_operation(self, operation: str) -> bool:
        for fallback in (False, True):
            try:
                provider = self._provider_for(operation, fallback)
            except EngineError:
                continue
            if provider is not None and callable(getattr(provider, operation, None)):
                return True
        return False

    def quota_status(self) -> dict | None:
        return self.quota.status() if self.quota else None

    @staticmethod
    def _validate(operation: str, value):
        if operation == "analyze_artwork":
            return ArtworkAnalysis.model_validate(value).model_dump()
        if operation == "verify_pattern_mockup":
            return MockupQC.model_validate(value).model_dump()
        if operation == "identify_parts":
            if not isinstance(value, list) or len(value) > 32:
                raise ValueError("Invalid candidates")
            result = [Candidate.model_validate(item).model_dump() for item in value]
            for candidate in result:
                x, y, width, height = candidate["candidate_bbox"]
                if (
                    min(x, y) < 0
                    or min(width, height) <= 0
                    or x + width > 1.001
                    or y + height > 1.001
                ):
                    raise ValueError("Invalid candidate coordinates")
            return result
        if operation in {
            "enhance_artwork",
            "create_pattern_mockup",
            "reconstruct_missing_part",
        }:
            if (
                not isinstance(value, Image.Image)
                or min(value.size) < 64
                or value.width * value.height > 8_000_000
            ):
                raise ValueError("Invalid generated image")
        return value

    def invoke(self, operation: str, *args) -> tuple[object, dict]:
        if operation not in OPERATIONS:
            raise EngineError("AI_CAPABILITY_UNAVAILABLE", "Unknown AI operation")

        started = time.perf_counter()
        attempts = list(self.configuration_errors)
        actual_attempts = 0
        primary_failed = False
        fallback_blocked = False

        for fallback_mode, mode in ((False, "primary_ai"), (True, "fallback_ai")):
            try:
                provider = self._provider_for(operation, fallback_mode)
            except EngineError as exc:
                attempts.append({"provider": "configuration", "code": exc.code})
                if not fallback_mode:
                    primary_failed = True
                continue

            if provider is None or not callable(getattr(provider, operation, None)):
                if not fallback_mode:
                    primary_failed = True
                continue

            quota_state = None
            direct_quota_reservation = False
            if fallback_mode and self.quota:
                quota_state = self.quota.status()
                if quota_state.get("remaining", 0) <= 0:
                    fallback_blocked = True
                    attempts.append(
                        {
                            "provider": provider.name,
                            "model": getattr(
                                provider,
                                "image_model"
                                if is_image_operation(operation)
                                else "model",
                                None,
                            ),
                            "code": "FALLBACK_DAILY_LIMIT_REACHED",
                            "dispatched": False,
                        }
                    )
                    break
                if hasattr(provider, "dispatch_hook"):
                    provider.dispatch_hook = self.quota.reserve
                    provider.last_quota_state = None
                else:
                    try:
                        quota_state = self.quota.reserve()
                        direct_quota_reservation = True
                    except EngineError as exc:
                        fallback_blocked = True
                        attempts.append(
                            {
                                "provider": provider.name,
                                "model": getattr(
                                    provider,
                                    "image_model"
                                    if is_image_operation(operation)
                                    else "model",
                                    None,
                                ),
                                "code": exc.code,
                                "dispatched": False,
                            }
                        )
                        break

            max_provider_attempts = (
                2
                if not fallback_mode and operation in PRIMARY_RETRY_OPERATIONS
                else 1
            )

            provider_succeeded = False
            for provider_attempt in range(1, max_provider_attempts + 1):
                try:
                    if hasattr(provider, "last_usage"):
                        provider.last_usage = {}
                    actual_attempts += 1
                    value = getattr(provider, operation)(*args)
                    if fallback_mode and self.quota and not direct_quota_reservation:
                        quota_state = getattr(provider, "last_quota_state", None)
                    value = self._validate(operation, value)
                    model = getattr(
                        provider,
                        "image_model"
                        if is_image_operation(operation)
                        else "model",
                        None,
                    )
                    provider_succeeded = True
                    return value, {
                        "processing_mode": mode,
                        "provider": provider.name,
                        "model": model,
                        "operation": operation,
                        "attempt_count": actual_attempts,
                        "duration_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "failures": attempts,
                        "fallback_quota": (
                            quota_state if fallback_mode else self.quota_status()
                        ),
                        "provider_usage": getattr(provider, "last_usage", {}),
                    }
                except Exception as exc:  # provider boundary must normalize failures
                    code = (
                        exc.code
                        if isinstance(exc, EngineError)
                        else "AI_RESPONSE_INVALID"
                    )
                    dispatched = True
                    if (
                        fallback_mode
                        and self.quota
                        and hasattr(provider, "last_quota_state")
                    ):
                        dispatched = (
                            getattr(provider, "last_quota_state", None) is not None
                        )

                    retry_scheduled = (
                        not fallback_mode
                        and provider_attempt < max_provider_attempts
                        and code in PRIMARY_RETRYABLE_CODES
                    )
                    attempts.append(
                        {
                            "provider": provider.name,
                            "model": getattr(
                                provider,
                                "image_model"
                                if is_image_operation(operation)
                                else "model",
                                None,
                            ),
                            "code": code,
                            "dispatched": dispatched,
                            "provider_attempt": provider_attempt,
                            "retry_scheduled": retry_scheduled,
                        }
                    )

                    if retry_scheduled:
                        continue

                    if not fallback_mode:
                        primary_failed = True
                    elif code == "FALLBACK_DAILY_LIMIT_REACHED":
                        fallback_blocked = True
                    break

            if provider_succeeded:
                break

        diagnostics = {"attempts": attempts}
        if self.quota:
            diagnostics["fallback_quota"] = self.quota_status()

        if primary_failed and fallback_blocked:
            raise EngineError(
                "AI_PROVIDER_UNAVAILABLE",
                "Primary AI could not complete the operation and fallback capacity is temporarily unavailable. Retry the operation.",
                status=503,
                diagnostics=diagnostics,
            )

        raise EngineError(
            "AI_PROVIDER_UNAVAILABLE",
            "No AI provider completed the request; retry or review server-side provider configuration",
            status=503,
            diagnostics=diagnostics,
        )