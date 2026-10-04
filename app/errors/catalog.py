"""Deterministic guidance also used when no online assistant is reachable."""

ACTION_ALLOWLIST = frozenset(
    {
        "retry_stage",
        "retry_part",
        "retry_job",
        "use_fallback_ai",
        "use_fallback_trace",
        "open_manual_editor",
        "return_to_detect_parts",
        "reselect_part",
        "revalidate",
        "view_validation_report",
        "view_error_details",
        "refresh_connection",
        "restart_upload",
    }
)
CATALOG = {
    "network": (
        "Connection issue",
        "The connection failed. Check your internet connection and server status.",
        ["refresh_connection", "view_error_details"],
    ),
    "upload": (
        "Upload issue",
        "The source could not be accepted. Use a valid JPG, PNG or WEBP within the upload limits.",
        ["restart_upload", "view_error_details"],
    ),
    "ai": (
        "AI provider issue",
        "An AI operation did not complete. Check provider configuration or continue with manual part selection.",
        ["return_to_detect_parts", "view_error_details"],
    ),
    "geometry": (
        "Vector geometry issue",
        "The geometry did not pass deterministic checks. Review the boundary or retry tracing.",
        ["open_manual_editor", "view_error_details"],
    ),
    "parts": (
        "Part review required",
        "A part is missing or uncertain. Select its region, create a proposal with AI, or leave the slot blank.",
        ["return_to_detect_parts", "view_error_details"],
    ),
    "validation": (
        "Validation blocked",
        "The output did not pass vector integrity checks. Correct the artwork and validate again.",
        ["revalidate", "view_validation_report", "view_error_details"],
    ),
    "export": (
        "Export issue",
        "A requested file could not be generated. Existing validated individual files are preserved.",
        ["view_error_details"],
    ),
    "auth": (
        "Access issue",
        "The request could not be authorized. Reconnect through the secured website.",
        ["refresh_connection", "view_error_details"],
    ),
    "job": (
        "Processing stopped",
        "The job stopped or exceeded a timeout. Completed parts remain stored; inspect details before retrying.",
        ["view_error_details"],
    ),
    "storage": (
        "Storage issue",
        "A required project file could not be read or saved. Contact the operator if this persists.",
        ["view_error_details"],
    ),
    "unknown": (
        "Processing issue",
        "The cause is not yet known. Inspect the safe diagnostic details before retrying.",
        ["view_error_details"],
    ),
}


def category(code):
    code = code.upper()
    for group, tokens in [
        (
            "network",
            (
                "NETWORK",
                "INTERNET",
                "REQUEST_TIMEOUT",
                "ENGINE_UNAVAILABLE",
                "SERVER",
                "HTTP_502",
                "HTTP_503",
            ),
        ),
        ("auth", ("AUTH", "PERMISSION", "IDENTITY", "ORIGIN")),
        ("upload", ("UPLOAD", "FILE_FORMAT", "FILE_TOO", "IMAGE", "MIME", "CORRUPT")),
        ("ai", ("AI_", "MOCKUP", "CLOUDFLARE", "API_KEY")),
        ("validation", ("VALIDATION", "RASTER", "ILLUSTRATOR")),
        ("geometry", ("GEOMETRY", "TRACE", "BOUNDARY", "VECTOR_SHAPE")),
        ("parts", ("PART", "DETECTION", "SEGMENT")),
        ("export", ("EXPORT", "PRODUCTION_PACK")),
        ("storage", ("STORAGE", "FILE_NOT_FOUND")),
        ("job", ("JOB", "WORKER", "QUEUE", "CANCEL")),
    ]:
        if any(token in code for token in tokens):
            return group
    return "unknown"


def supported_actions(error):
    actions = list(
        CATALOG[category(error.get("error_code", error.get("code", "UNKNOWN_ERROR")))][
            2
        ]
    )
    if error.get("retryable", error.get("recoverable")) and error.get("phase") in {
        "analyze",
        "correct-geometry",
        "segment",
        "prepare",
        "production",
        "recover-part",
        "vectorize",
        "optimize",
        "compose",
        "validate",
        "export",
    }:
        actions.insert(0, "retry_stage")
    if error.get("part_id"):
        actions = (
            ["retry_part", "use_fallback_trace", *actions]
            if error.get("phase") in {"vectorize", "production", "recover-part"}
            else actions
        )
    return list(dict.fromkeys(actions))


def local_help(error):
    title, explanation, _ = CATALOG[
        category(error.get("error_code", error.get("code", "UNKNOWN_ERROR")))
    ]
    actions = supported_actions(error)
    return {
        "title": title,
        "explanation": explanation,
        "likely_causes": [],
        "cause_status": "unknown",
        "severity": "recoverable"
        if error.get("retryable", error.get("recoverable", True))
        else "unknown",
        "recommended_action": actions[0],
        "secondary_actions": actions[1:],
        "user_message": explanation,
        "source": "deterministic_catalog",
        "supported_actions": actions,
    }
