"""Presets alter real curve tolerance, trace precision, and small-region filtering."""
PRESETS = {
    "FAST": {"epsilon": 1.8, "area_factor": 3, "color_precision": 4, "filter_speckle": 12, "path_precision": 2},
    "BALANCED": {"epsilon": .7, "area_factor": 1, "color_precision": 6, "filter_speckle": 4, "path_precision": 3},
    "PRECISION": {"epsilon": .3, "area_factor": .4, "color_precision": 7, "filter_speckle": 2, "path_precision": 4},
    "ULTRA": {"epsilon": .12, "area_factor": .15, "color_precision": 8, "filter_speckle": 1, "path_precision": 5},
}
