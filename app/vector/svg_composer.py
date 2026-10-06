"""Compose real geometry in named, editable groups without embedding raster masters."""
import copy
import json
import re
import cv2
import numpy as np
from xml.etree import ElementTree as ET
from shapely.geometry import Polygon, MultiPolygon
from app.vector.contour import SVG, contour_d
from app.models.project import Project, Part

LAYERS = ["CUT_PATH", "BLEED_PATH", "SAFE_ZONE", "BASE_COLOR", "GRADIENT", "PATTERN",
          "SIDE_GRAPHICS", "LOGO", "TEXT", "DETAILS"]


def namespace_ids(node, prefix: str):
    mapping = {e.get("id"): prefix + e.get("id") for e in node.iter() if e.get("id")}
    for e in node.iter():
        if e.get("id"):
            e.set("id", mapping[e.get("id")])
        for key, value in list(e.attrib.items()):
            for old, new in mapping.items():
                value = re.sub(r"url\(\s*['\"]?#" + re.escape(old) + r"['\"]?\s*\)", f"url(#{new})", value)
                if key.split("}")[-1] == "href" and value == "#" + old:
                    value = "#" + new
            e.set(key, value)


def boundary_paths(mask: np.ndarray, offset: float = 0) -> list[str]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    result = []
    for c in contours:
        points = c.reshape(-1, 2)
        if len(points) < 3:
            continue
        shape = Polygon(points)
        if not shape.is_valid:
            shape = shape.buffer(0)
        if offset:
            shape = shape.buffer(offset, quad_segs=8)
        shapes = list(shape.geoms) if isinstance(shape, MultiPolygon) else [shape]
        for p in shapes:
            if p.is_empty or not isinstance(p, Polygon):
                continue
            result.append(contour_d(np.asarray(p.exterior.coords[:-1], np.float32)))
    return result



SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}


def _shape_bbox(element) -> tuple[float, float, float, float] | None:
    """Return a local-coordinate bbox for direct vector shapes."""
    tag = element.tag.split("}")[-1]
    try:
        if tag == "path":
            path = parse_path(element.get("d", ""))
            if not path:
                return None
            xmin, xmax, ymin, ymax = path.bbox()
            return float(xmin), float(ymin), float(xmax), float(ymax)
        if tag == "rect":
            x, y = float(element.get("x", 0)), float(element.get("y", 0))
            w, h = float(element.get("width", 0)), float(element.get("height", 0))
            return x, y, x + w, y + h
        if tag == "circle":
            cx, cy, r = float(element.get("cx", 0)), float(element.get("cy", 0)), float(element.get("r", 0))
            return cx - r, cy - r, cx + r, cy + r
        if tag == "ellipse":
            cx, cy = float(element.get("cx", 0)), float(element.get("cy", 0))
            rx, ry = float(element.get("rx", 0)), float(element.get("ry", 0))
            return cx - rx, cy - ry, cx + rx, cy + ry
        if tag in {"polygon", "polyline"}:
            values = [float(v) for v in re.split(r"[ ,]+", element.get("points", "").strip()) if v]
            points = list(zip(values[0::2], values[1::2]))
            if not points:
                return None
            xs, ys = zip(*points)
            return min(xs), min(ys), max(xs), max(ys)
        if tag == "line":
            x1, y1 = float(element.get("x1", 0)), float(element.get("y1", 0))
            x2, y2 = float(element.get("x2", 0)), float(element.get("y2", 0))
            return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    except (ValueError, TypeError):
        return None
    return None


def _bbox_area(box) -> float:
    if not box:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _ocr_semantic(box, ocr_results: list[dict]) -> str | None:
    if not box:
        return None
    x1, y1, x2, y2 = box
    area = max(_bbox_area(box), 1.0)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    best = None
    best_score = 0.0
    for item in ocr_results or []:
        raw = item.get("bbox") or []
        if len(raw) != 4:
            continue
        ox, oy, ow, oh = [float(v) for v in raw]
        # Small expansion keeps outlined glyph edges assigned to the OCR object.
        pad = max(2.0, min(ow, oh) * 0.08)
        rx1, ry1, rx2, ry2 = ox - pad, oy - pad, ox + ow + pad, oy + oh + pad
        ix = max(0.0, min(x2, rx2) - max(x1, rx1))
        iy = max(0.0, min(y2, ry2) - max(y1, ry1))
        score = (ix * iy) / area
        center_inside = rx1 <= cx <= rx2 and ry1 <= cy <= ry2
        if (score >= 0.28 or center_inside) and score >= best_score:
            best, best_score = item, score
    if not best:
        return None
    value = str(best.get("text", "")).strip()
    compact = re.sub(r"\s+", "", value)
    if compact and any(ch.isdigit() for ch in compact) and not any(ch.isalpha() for ch in compact):
        return "NUMBER"
    if any(ch.isalpha() for ch in compact):
        return "NAME"
    return "OTHER"


def _semantic_destinations(layers: dict[str, ET.Element], prefix: str) -> dict[str, ET.Element]:
    text = layers["TEXT"]
    return {
        "BASE_COLOR": layers["BASE_COLOR"],
        "PATTERN": layers["PATTERN"],
        "DETAILS": layers["DETAILS"],
        "NAME": ET.SubElement(text, f"{{{SVG}}}g", {"id": prefix + "TEXT_NAME"}),
        "NUMBER": ET.SubElement(text, f"{{{SVG}}}g", {"id": prefix + "TEXT_NUMBER"}),
        "OTHER": ET.SubElement(text, f"{{{SVG}}}g", {"id": prefix + "TEXT_OTHER"}),
    }


def _place_editable_artwork(vector, layers: dict[str, ET.Element], part: Part, prefix: str, canvas_area: float):
    """Place direct trace shapes into stable Illustrator-friendly semantic groups.

    Native VTracer emits direct paths for the normal color pipeline. Those can be
    safely regrouped without changing their geometry. Nested/transformed groups
    are preserved intact in DETAILS so transforms and paint semantics are never
    silently destroyed.
    """
    destinations = _semantic_destinations(layers, prefix)
    children = list(vector)
    shape_boxes = [(child, _shape_bbox(child)) for child in children if child.tag.split("}")[-1] in SHAPE_TAGS]
    largest_area = max((_bbox_area(box) for _, box in shape_boxes), default=0.0)
    semantic_counts = {"BASE_COLOR": 0, "PATTERN": 0, "TEXT": 0, "DETAILS": 0}

    for child in children:
        tag = child.tag.split("}")[-1]
        if tag in {"defs", "linearGradient", "radialGradient"}:
            layers["GRADIENT"].append(child)
            continue
        if tag not in SHAPE_TAGS:
            layers["DETAILS"].append(child)
            semantic_counts["DETAILS"] += 1
            continue
        box = _shape_bbox(child)
        text_kind = _ocr_semantic(box, part.ocr_results)
        if text_kind:
            destinations[text_kind].append(child)
            semantic_counts["TEXT"] += 1
            child.set("data-semantic", "text-" + text_kind.lower())
            continue

        area = _bbox_area(box)
        if largest_area and area >= largest_area * 0.92:
            destinations["BASE_COLOR"].append(child)
            semantic_counts["BASE_COLOR"] += 1
            child.set("data-semantic", "base-color")
        elif canvas_area and 0 < area <= canvas_area * 0.035:
            destinations["PATTERN"].append(child)
            semantic_counts["PATTERN"] += 1
            child.set("data-semantic", "pattern-detail")
        else:
            destinations["DETAILS"].append(child)
            semantic_counts["DETAILS"] += 1
    return semantic_counts


def compose(project: Project, vectors: list[tuple[Part, ET.Element, np.ndarray]], size: tuple[int, int],
            *, single_part: bool = False) -> bytes:
    width, height = size
    attributes = {"version": "1.1", "viewBox": f"0 0 {width} {height}", "width": str(width), "height": str(height)}
    calibrated = project.settings.known_width_mm is not None
    mm_per_px = project.settings.known_width_mm / project.geometry["output_dimensions"][0] if calibrated else None
    if mm_per_px:
        attributes.update({"width": f"{width * mm_per_px:.6f}mm", "height": f"{height * mm_per_px:.6f}mm"})
    if single_part and vectors[0][0].physical_width_mm is not None:
        part = vectors[0][0]
        calibrated = True
        mm_per_px = part.physical_width_mm / width
        attributes.update({"width": f"{part.physical_width_mm:.6f}mm",
                           "height": f"{part.physical_height_mm:.6f}mm", "preserveAspectRatio": "none"})
    root = ET.Element(f"{{{SVG}}}svg", attributes)
    ET.SubElement(root, f"{{{SVG}}}title").text = project.name
    metadata = {"project_id": project.project_id, "engine_version": project.engine_version,
                "source_dimensions": project.source_metadata.get("normalized_dimensions"),
                "creation_date": project.created_at, "vectorization_mode": project.settings.vector_mode,
                "dimensions": "calibrated" if calibrated else "uncalibrated", "units": "mm" if calibrated else "px",
                "layers": "Illustrator groups include cut geometry, base color, pattern/details and OCR-separated text/number outlines.",
                "raster_policy": "No embedded raster", "part_ids": [p.part_id for p, _, _ in vectors]}
    metadata["part_dimensions_mm"] = {p.part_id: [p.physical_width_mm, p.physical_height_mm]
                                      for p, _, _ in vectors if p.physical_width_mm is not None}
    if not single_part and metadata["part_dimensions_mm"]:
        metadata["physical_handoff"] = "Individual part files carry their supplied dimensions; master preserves source layout."
    ET.SubElement(root, f"{{{SVG}}}metadata").text = json.dumps(metadata, sort_keys=True)
    for part, vector, mask in vectors:
        prefix = part.part_id + "_"
        x, y = (0, 0) if single_part else part.bbox[:2]
        group = ET.SubElement(root, f"{{{SVG}}}g", {"id": part.type.upper() + "_" + part.part_id,
                                                   "transform": f"translate({x},{y})"})
        ET.SubElement(group, f"{{{SVG}}}title").text = part.name
        layers = {name: ET.SubElement(group, f"{{{SVG}}}g", {"id": prefix + name}) for name in LAYERS}
        part_scale = part.physical_width_mm / part.bbox[2] if part.physical_width_mm else mm_per_px
        for name, offset in (("CUT_PATH", 0),
                             ("BLEED_PATH", (part.bleed_mm if part.physical_width_mm else project.settings.bleed_mm) / part_scale if part_scale else 0),
                             ("SAFE_ZONE", -(part.safe_zone_mm if part.physical_width_mm else project.settings.safe_zone_mm) / part_scale if part_scale else 0)):
            if name != "CUT_PATH" and not offset:
                continue
            layers[name].set("display", "none")
            # Buffer in physical space when independently supplied dimensions imply non-uniform scaling.
            if part.physical_width_mm and offset and part.physical_height_mm:
                sx, sy = part.physical_width_mm / part.bbox[2], part.physical_height_mm / part.bbox[3]
                from shapely.affinity import scale
                from shapely.geometry import Polygon
                outlines, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                paths = []
                for contour in outlines:
                    if len(contour) < 3:
                        continue
                    shape = Polygon(contour.reshape(-1, 2)).buffer(0)
                    physical = scale(shape, xfact=sx, yfact=sy, origin=(0, 0)).buffer(offset * sx, quad_segs=8)
                    back = scale(physical, xfact=1 / sx, yfact=1 / sy, origin=(0, 0))
                    pieces = list(back.geoms) if isinstance(back, MultiPolygon) else [back]
                    paths.extend(contour_d(np.asarray(piece.exterior.coords[:-1], np.float32)) for piece in pieces
                                 if isinstance(piece, Polygon) and not piece.is_empty)
            else:
                paths = boundary_paths(mask, offset)
            for index, d in enumerate(paths):
                ET.SubElement(layers[name], f"{{{SVG}}}path", {"id": prefix + name.lower() + f"_{index}",
                    "d": d, "fill": "none", "stroke": "#ff00ff", "stroke-width": "0.5"})
        vector = copy.deepcopy(vector)
        namespace_ids(vector, prefix)
        semantic_counts = _place_editable_artwork(
            vector,
            layers,
            part,
            prefix,
            float(max(1, part.bbox[2] * part.bbox[3])),
        )
        group.set("data-semantic-counts", json.dumps(semantic_counts, sort_keys=True))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def diagnostic(data: bytes) -> tuple[bytes, dict]:
    from defusedxml import ElementTree as SafeET
    from svgpathtools import parse_path
    root = SafeET.fromstring(data)
    objects = []
    for e in list(root.iter()):
        if e.tag.split("}")[-1] == "g" and e.get("id", "").endswith(("CUT_PATH", "BLEED_PATH", "SAFE_ZONE")):
            e.attrib.pop("display", None)
        if e.tag.split("}")[-1] != "path":
            continue
        e.set("fill", "none")
        e.set("stroke", "#7c3aed")
        e.set("stroke-width", ".45")
        path = parse_path(e.get("d", ""))
        objects.append({"id": e.get("id"), "anchors": len(path) + 1, "d": e.get("d"),
                        "transform": e.get("transform"), "bounds": list(path.bbox()) if path else None,
                        "nodes": [[s.start.real, s.start.imag] for s in path] +
                                 ([[path[-1].end.real, path[-1].end.imag]] if path else [])})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), {"objects": objects,
        "coordinate_space": "local path coordinates; SVG ancestor transforms must be applied by the frontend",
        "nodes": "Segment endpoints; Bezier controls remain available in path d commands."}
