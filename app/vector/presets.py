"""Presets alter real curve tolerance, trace precision, and small-region filtering.

BALANCED is the default production profile and intentionally preserves jersey
typography, numbers, fine trim, sponsor marks and narrow linework. FAST remains
the only intentionally aggressive simplification preset.
"""
PRESETS = {
    "FAST": {"epsilon": 1.8, "area_factor": 3, "color_precision": 4, "filter_speckle": 12, "path_precision": 2},
    "BALANCED": {"epsilon": .30, "area_factor": .40, "color_precision": 8, "filter_speckle": 2, "path_precision": 5},
    "PRECISION": {"epsilon": .16, "area_factor": .20, "color_precision": 8, "filter_speckle": 1, "path_precision": 6},
    "ULTRA": {"epsilon": .08, "area_factor": .10, "color_precision": 8, "filter_speckle": 1, "path_precision": 7},
}
