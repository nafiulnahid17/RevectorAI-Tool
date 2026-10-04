import cv2
import numpy as np
from PIL import Image
from app.models.project import ProcessingSettings
from app.pipeline.colors import quantize, PALETTE_VERSION


def reconstruct(image: Image.Image, settings: ProcessingSettings):
    rgba = np.asarray(image.convert("RGBA")).copy()
    if settings.noise_reduction:
        # Edge-preserving modest bilateral cleanup; alpha is kept unchanged.
        rgba[..., :3] = cv2.bilateralFilter(rgba[..., :3], 5, 12, 3)
    reference = Image.fromarray(rgba)
    trace, palette, _ = quantize(reference, settings.max_colors, settings.delta_e)
    return reference, trace, palette, {
        "operations": ["mask_background_removal"] + (["bilateral_denoise_sigma12"] if settings.noise_reduction else []),
        "shadow_reconstruction": "not_applied", "glare_reconstruction": "not_applied",
        "color_normalization": "preserved", "reference_dimensions": list(reference.size),
        "quantization": {"algorithm": PALETTE_VERSION, "max_colors": settings.max_colors, "delta_e_cie76": settings.delta_e},
    }
