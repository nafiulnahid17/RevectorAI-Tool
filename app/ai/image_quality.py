"""AI raster targets for the ReVector workspace.

User-facing presets intentionally control mockup/image-generation size:
BALANCED -> 960x720, FAST/"Clean" -> 1440x1080, ULTRA/"Detailed" -> 1920x1440.
"""

PRESET_TARGETS_4_3 = {
    "BALANCED": (960, 720),
    "FAST": (1440, 1080),
    "PRECISION": (1920, 1440),
    "ULTRA": (1920, 1440),
}

PRESET_TARGETS_1_1 = {
    "BALANCED": (720, 720),
    "FAST": (1080, 1080),
    "PRECISION": (1440, 1440),
    "ULTRA": (1440, 1440),
}

# Legacy image_quality support remains for existing projects/API clients.
QUALITY_TARGETS_4_3 = {
    "LOW": (960, 720),
    "MEDIUM": (1440, 1080),
    "HIGH": (1920, 1440),
    "MAX": (1920, 1440),
}

QUALITY_TARGETS_1_1 = {
    "LOW": (720, 720),
    "MEDIUM": (1080, 1080),
    "HIGH": (1440, 1440),
    "MAX": (1440, 1440),
}


def target_dimensions(quality: str, aspect_ratio: str = "4:3") -> tuple[int, int]:
    quality = str(quality or "MEDIUM").upper()
    table = QUALITY_TARGETS_1_1 if aspect_ratio == "1:1" else QUALITY_TARGETS_4_3
    return table.get(quality, table["MEDIUM"])


def preset_target_dimensions(preset: str, aspect_ratio: str = "4:3") -> tuple[int, int]:
    preset = str(preset or "BALANCED").upper()
    table = PRESET_TARGETS_1_1 if aspect_ratio == "1:1" else PRESET_TARGETS_4_3
    return table.get(preset, table["BALANCED"])


def preset_quality_label(preset: str) -> str:
    return {
        "BALANCED": "720P",
        "FAST": "1080P",
        "PRECISION": "1440P",
        "ULTRA": "1440P",
    }.get(str(preset or "BALANCED").upper(), "720P")


def nearest_openrouter_resolution(size: tuple[int, int]) -> str:
    # 720p requests use the 1K provider tier. 1080p/1440p requests ask for 2K
    # and are downscaled by the workflow only when the provider returns larger.
    longest = max(size)
    return "1K" if longest <= 1000 else "2K"


def nearest_supported_ratio(size: tuple[int, int]) -> str:
    width, height = size
    ratio = width / max(height, 1)
    choices = {
        "1:1": 1.0,
        "4:3": 4 / 3,
        "3:4": 3 / 4,
        "16:9": 16 / 9,
        "9:16": 9 / 16,
    }
    return min(choices, key=lambda name: abs(choices[name] - ratio))
