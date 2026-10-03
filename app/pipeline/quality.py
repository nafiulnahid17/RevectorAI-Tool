"""Measured diagnostics; flags are heuristics, never fabricated probabilities."""
import cv2
import numpy as np
from PIL import Image


def analyze(image: Image.Image) -> dict:
    rgb = np.asarray(image.convert("RGB"))
    # Analysis only is downsampled; masters retain their dimensions.
    scale = min(1.0, 1600 / max(image.size))
    if scale < 1:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    laplacian = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    residual = gray - cv2.medianBlur(gray.astype(np.uint8), 3).astype(np.float32)
    noise = float(np.median(np.abs(residual - np.median(residual))) * 1.4826 / 255)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    glare = float(np.mean((hsv[..., 2] > 248) & (hsv[..., 1] < 20)))
    shadow = float(np.mean(gray < 35))
    block_diffs = [np.mean(np.abs(np.diff(gray, axis=axis))) for axis in [0, 1]]
    boundary = [np.mean(np.abs(np.diff(gray, axis=0)[7::8])) if gray.shape[0] > 16 else 0,
                np.mean(np.abs(np.diff(gray, axis=1)[:, 7::8])) if gray.shape[1] > 16 else 0]
    blocking = float(max(0, np.mean(boundary) - np.mean(block_diffs)) / 255)
    from app.pipeline.geometry import detect_quad
    quad = detect_quad(Image.fromarray(rgb))
    perspective = False
    if quad is not None:
        edges = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        perspective = bool(abs(edges[0] - edges[2]) / max(edges[0], edges[2], 1) > .08
                           or abs(edges[1] - edges[3]) / max(edges[1], edges[3], 1) > .08)
    quantized = Image.fromarray(rgb).quantize(colors=8)
    palette = quantized.getpalette()
    colors = sorted(quantized.getcolors(), reverse=True)
    return {
        "resolution": {"width": image.width, "height": image.height, "aspect_ratio": image.width / image.height},
        "analysis_scale": scale,
        "quality": {"sharpness_laplacian_variance": laplacian, "blur_score": 1 / (1 + laplacian / 100),
                    "contrast": float(gray.std() / 255), "brightness": float(gray.mean() / 255),
                    "noise_score": noise, "compression_blocking_estimate": blocking,
                    "possible_glare_fraction": glare, "possible_shadow_fraction": shadow,
                    "perspective_issue": perspective, "lighting_issue": glare > .1 or shadow > .25},
        "dominant_colors": [{"rgb": palette[index * 3:index * 3 + 3], "fraction": count / gray.size}
                            for count, index in colors],
        "method_notes": ["Blur, glare, shadows, JPEG blocking and perspective are heuristics; solid white/black graphics can trigger lighting flags."]
    }
