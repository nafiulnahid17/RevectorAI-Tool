"""Color masks become closed editable paths with even-odd holes."""
import cv2
import numpy as np
from PIL import Image
from xml.etree import ElementTree as ET
from app.pipeline.colors import quantize
from app.vector.presets import PRESETS
from app.models.project import ProcessingSettings

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def contour_d(contour: np.ndarray) -> str:
    points = contour.reshape(-1, 2)
    if len(points) < 3:
        return ""
    return "M " + " L ".join(f"{float(x):.3f},{float(y):.3f}" for x, y in points) + " Z"


def trace(image: Image.Image, settings: ProcessingSettings, mono: bool = False) -> tuple[ET.Element, dict]:
    preset = PRESETS[settings.preset]
    if mono:
        rgba = np.asarray(image.convert("RGBA"))
        gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        labels = np.where((binary > 0) & (rgba[..., 3] > 8), 0, -1)
        palette = [{"index": 0, "hex": "#000000"}]
    else:
        _, palette, labels = quantize(image, settings.max_colors, settings.delta_e)
    root = ET.Element(f"{{{SVG}}}svg", {"viewBox": f"0 0 {image.width} {image.height}",
                                       "width": str(image.width), "height": str(image.height)})
    paths, raw_anchors = 0, 0
    threshold = settings.min_region_area * preset["area_factor"]
    for color in palette:
        mask = np.uint8(labels == color["index"]) * 255
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        if hierarchy is None:
            continue
        for index, contour in enumerate(contours):
            if hierarchy[0, index, 3] != -1 or abs(cv2.contourArea(contour)) < threshold:
                continue
            # Pixel-center contours have a half-pixel sampling offset, reported by visual validation.
            d = contour_d(contour)
            if not d:
                continue
            raw_anchors += len(contour)
            child = hierarchy[0, index, 2]
            while child != -1:
                if abs(cv2.contourArea(contours[child])) >= threshold:
                    d += " " + contour_d(contours[child])
                    raw_anchors += len(contours[child])
                child = hierarchy[0, child, 0]
            ET.SubElement(root, f"{{{SVG}}}path", {"id": f"shape_{paths:05d}", "fill": color["hex"],
                                                  "fill-rule": "evenodd", "d": d})
            paths += 1
    return root, {"backend": "opencv_color_contours" if not mono else "opencv_mono_contours",
                  "initial_anchor_count": raw_anchors, "initial_path_count": paths}
