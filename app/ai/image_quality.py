"""AI raster quality targets, intentionally independent from deterministic vector presets."""

QUALITY_TARGETS_4_3 = {
    "LOW": (960, 720),
    "MEDIUM": (1440, 1080),
    "HIGH": (1920, 1440),
    "MAX": (2560, 1920),
}

QUALITY_TARGETS_1_1 = {
    "LOW": (720, 720),
    "MEDIUM": (1080, 1080),
    "HIGH": (1440, 1440),
    "MAX": (1920, 1920),
}


def target_dimensions(quality: str, aspect_ratio: str = "4:3") -> tuple[int, int]:
    quality = str(quality or "MEDIUM").upper()
    table = QUALITY_TARGETS_1_1 if aspect_ratio == "1:1" else QUALITY_TARGETS_4_3
    return table.get(quality, table["MEDIUM"])


def nearest_openrouter_resolution(size: tuple[int, int]) -> str:
    longest = max(size)
    return "1K" if longest <= 1600 else "2K"


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
