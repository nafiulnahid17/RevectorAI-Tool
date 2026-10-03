"""Conservative global smooth linear gradient fitting with measured residual rejection."""
import numpy as np
from PIL import Image
from xml.etree import ElementTree as ET
from app.vector.contour import SVG, contour_d
import cv2


def fit_linear(image: Image.Image, max_error: float = 9) -> tuple[ET.Element, dict] | None:
    rgba = np.asarray(image.convert("RGBA"))
    ys, xs = np.where(rgba[..., 3] > 250)
    if len(xs) < 100:
        return None
    stride = max(1, len(xs) // 30000)
    x, y = xs[::stride] / max(1, image.width - 1), ys[::stride] / max(1, image.height - 1)
    colors = rgba[ys[::stride], xs[::stride], :3].astype(float)
    design = np.column_stack([np.ones(len(x)), x, y])
    coefficients, *_ = np.linalg.lstsq(design, colors, rcond=None)
    rmse = float(np.sqrt(np.mean((design @ coefficients - colors) ** 2)))
    u, singular, _ = np.linalg.svd(coefficients[1:], full_matrices=False)
    if rmse > max_error or singular[0] < 30 or singular[1] / singular[0] > .12:
        return None
    axis = u[:, 0]
    projection = np.column_stack([x, y]) @ axis
    lo, hi = float(projection.min()), float(projection.max())
    center = np.array([.5, .5])
    start = center + axis * (lo - center @ axis)
    end = center + axis * (hi - center @ axis)
    stops = []
    for t in (lo, (lo + hi) / 2, hi):
        point = center + axis * (t - center @ axis)
        color = np.clip(np.rint(np.array([1, *point]) @ coefficients), 0, 255).astype(int)
        stops.append("#" + "".join(f"{v:02x}" for v in color))
    root = ET.Element(f"{{{SVG}}}svg", {"viewBox": f"0 0 {image.width} {image.height}",
                                       "width": str(image.width), "height": str(image.height)})
    defs = ET.SubElement(root, f"{{{SVG}}}defs")
    gradient = ET.SubElement(defs, f"{{{SVG}}}linearGradient", {
        "id": "smooth_gradient", "gradientUnits": "userSpaceOnUse",
        "x1": str(start[0] * (image.width - 1)), "y1": str(start[1] * (image.height - 1)),
        "x2": str(end[0] * (image.width - 1)), "y2": str(end[1] * (image.height - 1))})
    for offset, color in zip(("0", ".5", "1"), stops):
        ET.SubElement(gradient, f"{{{SVG}}}stop", {"offset": offset, "stop-color": color})
    contours, hierarchy = cv2.findContours(rgba[..., 3], cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    d = " ".join(contour_d(c) for c in contours if len(c) >= 3)
    if not d:
        return None
    ET.SubElement(root, f"{{{SVG}}}path", {"id": "gradient_shape", "d": d,
                                          "fill": "url(#smooth_gradient)", "fill-rule": "evenodd"})
    return root, {"backend": "linear_gradient_fit", "rmse_rgb": rmse, "stops": stops,
                  "method": "rank_one_affine_rgb_fit", "radial_gradients": "not_implemented"}
