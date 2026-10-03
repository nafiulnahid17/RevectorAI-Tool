"""Cleanup preserves curves and uses bounded Douglas-Peucker simplification on line rings."""
import re
import numpy as np
import cv2
from svgpathtools import parse_path, Line
from shapely.geometry import Polygon
from app.vector.presets import PRESETS


def anchor_count(d: str) -> int:
    return len(parse_path(d)) + len(re.findall(r"[Mm]", d))


def simplify_path(d: str, epsilon: float) -> tuple[str, list[str]]:
    path = parse_path(d)
    warnings = []
    subpaths = path.continuous_subpaths()
    # Cubic/arc paths are retained; merging them without an error guarantee would be unsafe.
    if not all(isinstance(s, Line) for s in path):
        return d, warnings
    pieces = []
    for sub in subpaths:
        if not sub:
            continue
        closed = sub.isclosed()
        points = np.array([[s.start.real, s.start.imag] for s in sub] +
                          [[sub[-1].end.real, sub[-1].end.imag]], dtype=np.float32)
        if closed:
            points = points[:-1]
        if len(points) >= 3 and closed and not Polygon(points).is_valid:
            warnings.append("Self-intersection detected; fill-rule retained and simplification skipped for this ring.")
            pieces.append("M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in points) + " Z")
            continue
        reduced = cv2.approxPolyDP(points.reshape(-1, 1, 2), epsilon, closed).reshape(-1, 2)
        if len(reduced) < (3 if closed else 2):
            reduced = points
        if closed and not Polygon(reduced).is_valid:
            reduced = points
        # approxPolyDP bounds deviation from source linework in working pixels.
        pieces.append("M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in reduced) + (" Z" if closed else ""))
    return " ".join(pieces), warnings


def optimize(root, preset: str) -> dict:
    before_paths = before_anchors = after_anchors = removed = 0
    seen = set()
    warnings = []
    for parent in root.iter():
        for child in list(parent):
            if child.tag.split("}")[-1] != "path":
                continue
            d = child.get("d", "")
            before_paths += 1
            before_anchors += anchor_count(d)
            attributes = tuple(sorted((k, v) for k, v in child.attrib.items() if k != "id"))
            # Deduplicate only adjacent identical siblings: removing a later painted
            # copy across intervening colors can change z-order and visual appearance.
            signature = (id(parent), attributes)
            if signature in seen:
                parent.remove(child)
                removed += 1
                continue
            seen = {signature}
            new_d, notes = simplify_path(d, PRESETS[preset]["epsilon"])
            child.set("d", new_d)
            after_anchors += anchor_count(new_d)
            warnings.extend(notes)
    return {"before_paths": before_paths, "after_paths": before_paths - removed,
            "initial_anchor_count": before_anchors, "final_anchor_count": after_anchors,
            "simplification_ratio": 1 - after_anchors / max(1, before_anchors),
            "line_tolerance_px": PRESETS[preset]["epsilon"], "warnings": list(set(warnings)),
            "curve_cleanup": "Existing cubic curves retained; no unbounded curve fitting applied."}
