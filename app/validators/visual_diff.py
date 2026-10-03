"""Deterministic resvg rendering and advisory image similarity metrics."""
from io import BytesIO
import numpy as np
from PIL import Image, ImageChops
from skimage.metrics import structural_similarity
import cv2
from app.core.exceptions import EngineError
from app.validators.svg_validator import validate_svg


def render_svg(data: bytes, width: int | None = None, height: int | None = None) -> Image.Image:
    report = validate_svg(data)
    if not report["valid_svg"]:
        raise EngineError("SVG_VALIDATION_FAILED", "Rendering refused: unsafe or invalid SVG")
    if width and height and width * height > 40_000_000:
        raise EngineError("INVALID_DIMENSIONS", "Render exceeds pixel limit")
    try:
        import resvg_py
        if width and height:
            # resvg's target width/height options preserve intrinsic aspect ratio.
            # For a normalized comparison canvas, explicitly set that viewport;
            # the stored SVG retains its physical dimensions without modification.
            from defusedxml import ElementTree as SafeET
            from xml.etree import ElementTree as ET
            root = SafeET.fromstring(data)
            root.set("width", str(width))
            root.set("height", str(height))
            data = ET.tostring(root, encoding="utf-8")
        # Explicit SVG/CSS DPI is required for mm/cm units; resvg-py's zero default
        # produces an invalid intrinsic size for physically calibrated artwork.
        result = resvg_py.svg_to_bytes(svg_string=data.decode(), width=width, height=height, dpi=96.0)
        image = Image.open(BytesIO(result)).convert("RGBA")
        image.load()
        return image
    except ImportError as exc:
        raise EngineError("RENDERER_UNAVAILABLE", "Install resvg-py to validate rendering") from exc
    except Exception as exc:
        raise EngineError("SVG_RENDER_FAILED", "resvg could not render the validated SVG") from exc


def white_background(image: Image.Image) -> Image.Image:
    result = Image.new("RGBA", image.size, "white")
    return Image.alpha_composite(result, image.convert("RGBA")).convert("RGB")


def compare(reference: Image.Image, vector: Image.Image) -> tuple[dict, Image.Image]:
    if reference.size != vector.size:
        raise EngineError("VISUAL_DIMENSION_MISMATCH", "Comparison images must have equal dimensions")
    ref, vec = white_background(reference), white_background(vector)
    a, b = np.asarray(ref), np.asarray(vec)
    diff = ImageChops.difference(ref, vec)
    # Metrics are sampled for large images; full-size preview/difference artifacts are retained.
    scale = min(1, 2000 / max(ref.size))
    if scale < 1:
        a = cv2.resize(a, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        b = cv2.resize(b, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ga, gb = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)
    edges_a, edges_b = cv2.Canny(ga, 70, 160) > 0, cv2.Canny(gb, 70, 160) > 0
    edge_iou = float(np.logical_and(edges_a, edges_b).sum() / max(1, np.logical_or(edges_a, edges_b).sum()))
    lab_a = cv2.cvtColor(a.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    lab_b = cv2.cvtColor(b.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    win = min(7, min(a.shape[:2]) if min(a.shape[:2]) % 2 else min(a.shape[:2]) - 1)
    ssim = float(structural_similarity(a, b, channel_axis=2, data_range=255, win_size=win)) if win >= 3 else None
    return {"ssim": ssim, "mean_absolute_rgb_error": float(np.abs(a.astype(float) - b).mean()),
            "mean_delta_e_cie76": float(np.linalg.norm(lab_a - lab_b, axis=2).mean()),
            "edge_iou": edge_iou, "metric_scale": scale,
            "advisory": True, "note": "Similarity does not establish production fidelity or pixel perfection."}, diff
