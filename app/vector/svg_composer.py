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
                "layers": "Geometric layers; garment-side/logo identity is not inferred by CV.",
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
        has_gradient = any(e.tag.split("}")[-1] in {"linearGradient", "radialGradient"} for e in vector.iter())
        destination = layers["GRADIENT"] if has_gradient else layers["DETAILS"]
        # Keep backend paint order and transforms intact. No invented semantic decomposition.
        for child in list(vector):
            destination.append(child)
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
