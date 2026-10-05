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
    "analyze_artwork": "openai/gpt-6-astra",
    "identify_parts": "openai/gpt-6-astra",
    "enhance_artwork": "openai/gpt-image-2.5-sunburst",
    "create_pattern_mockup": "openai/gpt-image-2.5-sunburst",
    "verify_pattern_mockup": "google/gemini-3.1-pro-preview",
    "reconstruct_missing_part": "openai/gpt-image-2.5-sunburst",
    "explain_error": "google/gemini-3.8-flash",
}

FALLBACK_MODELS = {
    "analyze_artwork": "google/gemini-3.8-flash",
    "identify_parts": "google/gemini-3.8-flash",
    "enhance_artwork": "openai/gpt-image-2.5-flare",
    "create_pattern_mockup": "openai/gpt-image-2.5-flare",
    "verify_pattern_mockup": "google/gemini-3.8-flash",
    "reconstruct_missing_part": "openai/gpt-image-2.5-flare",
    "explain_error": "google/gemini-3.1-pro-preview",
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
