"""Recorded, explicit homographies. Automatic guesses require an explicit auto request."""
import cv2
import numpy as np
from PIL import Image
from app.core.exceptions import EngineError


def order_quad(points) -> np.ndarray:
    p = np.asarray(points, dtype=np.float32)
    if p.shape != (4, 2) or not np.isfinite(p).all():
        raise EngineError("INVALID_CORNERS", "Four finite corner coordinates are required")
    # Angular sorting avoids the duplicate-corner sum/difference failure on diamonds.
    angles = np.arctan2(p[:, 1] - p[:, 1].mean(), p[:, 0] - p[:, 0].mean())
    p = p[np.argsort(angles)]
    p = np.roll(p, -np.argmin(p.sum(axis=1)), axis=0)
    if not cv2.isContourConvex(p) or abs(cv2.contourArea(p)) < 4:
        raise EngineError("INVALID_CORNERS", "Corners must form a nondegenerate convex quadrilateral")
    return p


def detect_quad(image: Image.Image) -> np.ndarray | None:
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
        if cv2.contourArea(contour) < image.width * image.height * .2:
            continue
        approx = cv2.approxPolyDP(contour, .02 * cv2.arcLength(contour, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return order_quad(approx.reshape(-1, 2))
    return None


def correct(image: Image.Image, corners=None, auto: bool = False) -> tuple[Image.Image, dict]:
    if corners is None and not auto:
        return image.copy(), {"method": "identity", "matrix": np.eye(3).tolist(),
                              "output_dimensions": list(image.size), "crop_applied": False}
    points = detect_quad(image) if corners is None else order_quad(corners)
    if points is None:
        raise EngineError("PERSPECTIVE_DETECTION_FAILED", "No reliable quadrilateral found; provide four manual corners")
    if np.any(points < 0) or np.any(points[:, 0] > image.width - 1) or np.any(points[:, 1] > image.height - 1):
        raise EngineError("INVALID_CORNERS", "Corners must lie within the working image")
    tl, tr, br, bl = points
    width = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))) + 1
    height = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))) + 1
    target = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    matrix = cv2.getPerspectiveTransform(points, target)
    warped = cv2.warpPerspective(np.asarray(image.convert("RGBA")), matrix, (width, height),
                                 flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT)
    return Image.fromarray(warped), {"method": "automatic_homography" if corners is None else "manual_homography",
                                   "corners": points.tolist(), "matrix": matrix.tolist(),
                                   "inverse_matrix": np.linalg.inv(matrix).tolist(),
                                   "output_dimensions": [width, height], "crop_applied": True,
                                   "discarded_area_recorded": "All source pixels outside the recorded quadrilateral are excluded."}
