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
    # OpenRouter's current GA low-latency Gemini 3.1 text/vision route.
    "analyze_artwork": "google/gemini-3.1-flash-lite",
    "identify_parts": "google/gemini-3.1-flash-lite",
    "verify_pattern_mockup": "google/gemini-3.1-flash-lite",
    "explain_error": "google/gemini-3.1-flash-lite",
    # Gemini 3.1 Flash Image (Nano Banana 2) for all reference-image generation.
    "enhance_artwork": "google/gemini-3.1-flash-image",
    "create_pattern_mockup": "google/gemini-3.1-flash-image",
    "reconstruct_missing_part": "google/gemini-3.1-flash-image",
}

FALLBACK_MODELS = {
    # Keep fallback inside the Gemini 3.1 Flash family.
    "analyze_artwork": "google/gemini-3.1-flash-lite-preview",
    "identify_parts": "google/gemini-3.1-flash-lite-preview",
    "verify_pattern_mockup": "google/gemini-3.1-flash-lite-preview",
    "explain_error": "google/gemini-3.1-flash-lite-preview",
    "enhance_artwork": "google/gemini-3.1-flash-lite-image",
    "create_pattern_mockup": "google/gemini-3.1-flash-lite-image",
    "reconstruct_missing_part": "google/gemini-3.1-flash-lite-image",
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
