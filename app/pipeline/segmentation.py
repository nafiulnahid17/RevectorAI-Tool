"""Conservative foreground masks; garment semantic labels require user/model evidence."""
import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from app.core.exceptions import EngineError


def manual_mask(size: tuple[int, int], polygon: list) -> np.ndarray:
    points = np.asarray(polygon, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise EngineError("INVALID_BOUNDARY", "Boundary needs at least three finite coordinate pairs")
    width, height = size
    if np.any(points < 0) or np.any(points[:, 0] > width - 1) or np.any(points[:, 1] > height - 1):
        raise EngineError("INVALID_BOUNDARY", "Boundary is outside corrected image coordinates")
    shape = Polygon(points)
    if not shape.is_valid or shape.area < 4:
        raise EngineError("INVALID_BOUNDARY", "Boundary must be a simple polygon with nonzero area")
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [np.rint(points).astype(np.int32)], 255)
    return mask


def detect_masks(image: Image.Image, min_ratio: float) -> tuple[list[np.ndarray], dict]:
    rgba = np.asarray(image.convert("RGBA"))
    h, w = rgba.shape[:2]
    if np.any(rgba[..., 3] < 250):
        mask = np.uint8(rgba[..., 3] > 8) * 255
        method = "source_alpha"
    else:
        rgb = rgba[..., :3]
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        bg = np.median(border, axis=0).astype(np.uint8)
        lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
        bg_lab = cv2.cvtColor(bg.reshape(1, 1, 3).astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[0, 0]
        distance = np.linalg.norm(lab - bg_lab, axis=2)
        mask = np.uint8(distance > 12) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        method = "border_color_distance_contours"
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    masks = []
    for contour in sorted(contours, key=lambda c: cv2.boundingRect(c)[:2]):
        if cv2.contourArea(contour) < w * h * min_ratio:
            continue
        m = np.zeros((h, w), np.uint8)
        cv2.drawContours(m, [contour], -1, 255, cv2.FILLED)
        m = cv2.bitwise_and(m, rgba[..., 3])
        masks.append(m)
    warnings = ["CV masks do not establish front/back/left/right garment identity; confirm boundaries manually."]
    if not masks:
        masks = [rgba[..., 3].copy()]
        method = "whole_artboard_fallback"
        warnings.append("No separate panel boundary found; full artboard retained as one unknown part.")
    return masks, {"method": method, "warnings": warnings, "confidence": None}


def crop_mask(image: Image.Image, mask: np.ndarray) -> tuple[Image.Image, tuple[int, int, int, int], Image.Image]:
    ys, xs = np.where(mask > 0)
    if not len(xs):
        raise EngineError("EMPTY_SEGMENT", "Part mask has no foreground pixels")
    x, y, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    rgba = np.asarray(image.convert("RGBA")).copy()[y:bottom, x:right]
    rgba[..., 3] = np.minimum(rgba[..., 3], mask[y:bottom, x:right])
    return Image.fromarray(rgba), (x, y, right - x, bottom - y), Image.fromarray(mask[y:bottom, x:right])
