"""Central production model profile. Model slugs are configuration data, never workflow logic."""

OPERATIONS = (
    "analyze_artwork",
    "enhance_artwork",
    "create_pattern_mockup",
    "verify_pattern_mockup",
    "identify_parts",
    "reconstruct_missing_part",
    "explain_error",
)

IMAGE_OPERATIONS = frozenset({
    "enhance_artwork",
    "create_pattern_mockup",
    "reconstruct_missing_part",
})

DEFAULT_MODELS = {
    # GPT-6 Luna is currently cheaper than Gemini 3.1 Flash Lite on OpenRouter,
    # accepts image input, and has native JSON-Schema structured outputs.
    "analyze_artwork": "openai/gpt-6-luna",
    "identify_parts": "openai/gpt-6-luna",
    "verify_pattern_mockup": "openai/gpt-6-luna",
    "explain_error": "openai/gpt-6-luna",
    # Nano Banana 2 Lite remains the fastest/cost-efficient image generation route.
    "enhance_artwork": "google/gemini-3.1-flash-lite-image:nitro",
    "create_pattern_mockup": "google/gemini-3.1-flash-lite-image:nitro",
    "reconstruct_missing_part": "google/gemini-3.1-flash-lite-image:nitro",
}

FALLBACK_MODELS = {
    # Gemini stays as a cross-vendor structured-output fallback.
    "analyze_artwork": "google/gemini-3.1-flash-lite",
    "identify_parts": "google/gemini-3.1-flash-lite",
    "verify_pattern_mockup": "google/gemini-3.1-flash-lite",
    "explain_error": "google/gemini-3.1-flash-lite",
    # Image fallback trades some cost for quality/reliability and is rarely used.
    "enhance_artwork": "google/gemini-3.1-flash-image:nitro",
    "create_pattern_mockup": "google/gemini-3.1-flash-image:nitro",
    "reconstruct_missing_part": "google/gemini-3.1-flash-image:nitro",
}

ENV_FIELDS = {
    "analyze_artwork": "analyze_model",
    "identify_parts": "identify_model",
    "enhance_artwork": "enhance_model",
    "create_pattern_mockup": "mockup_model",
    "verify_pattern_mockup": "mockup_qc_model",
    "reconstruct_missing_part": "missing_part_model",
    "explain_error": "error_model",
}

FALLBACK_ENV_FIELDS = {
    "analyze_artwork": "analyze_fallback_model",
    "identify_parts": "identify_fallback_model",
    "enhance_artwork": "enhance_fallback_model",
    "create_pattern_mockup": "mockup_fallback_model",
    "verify_pattern_mockup": "mockup_qc_fallback_model",
    "reconstruct_missing_part": "missing_part_fallback_model",
    "explain_error": "error_fallback_model",
}

CAPABILITY_REQUIREMENTS = {
    "analyze_artwork": ("vision", "structured_json"),
    "identify_parts": ("vision", "structured_json"),
    "enhance_artwork": ("image_generation", "reference_image"),
    "create_pattern_mockup": ("image_generation", "reference_image"),
    "verify_pattern_mockup": ("vision", "structured_json"),
    "reconstruct_missing_part": ("image_generation", "reference_image"),
    "explain_error": ("text", "structured_json"),
}


def resolve_model(settings, operation: str, fallback: bool = False) -> str:
    if operation not in OPERATIONS:
        return ""
    field = (FALLBACK_ENV_FIELDS if fallback else ENV_FIELDS)[operation]
    override = getattr(settings, field, "") or ""
    return override.strip() or (FALLBACK_MODELS if fallback else DEFAULT_MODELS)[operation]


def resolved_profile(settings) -> dict:
    return {
        operation: {
            "primary": resolve_model(settings, operation),
            "fallback": resolve_model(settings, operation, True),
            "capabilities": list(CAPABILITY_REQUIREMENTS[operation]),
        }
        for operation in OPERATIONS
    }


def is_image_operation(operation: str) -> bool:
    return operation in IMAGE_OPERATIONS
