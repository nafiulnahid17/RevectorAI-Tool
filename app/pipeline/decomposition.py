from PIL import Image
import numpy as np
import cv2


def decompose(image: Image.Image, palette: list[dict]) -> dict:
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 70, 160)
    density = float(np.mean(edges > 0))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return {"edge_density": density, "edge_component_count": len(contours),
            "region_classes": ["COMPLEX_TEXTURE" if density > .15 else "SOLID_SHAPE"],
            "classification_method": "edge_density_heuristic", "semantic_logo_detection": "unavailable",
            "pattern_detection": "not_implemented", "palette_size": len(palette)}
