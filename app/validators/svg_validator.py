"""Fail-closed SVG inspection before rendering/export. Never trust a file extension."""
import math
import re
from collections import Counter
from defusedxml import ElementTree as SafeET
from svgpathtools import parse_path
from app.validators.raster_detector import detect, decoded

SVG = "http://www.w3.org/2000/svg"
ALLOWED = {"svg", "g", "defs", "metadata", "title", "desc", "path", "rect", "circle", "ellipse",
           "polygon", "polyline", "line", "text", "tspan", "clipPath", "mask", "linearGradient",
           "radialGradient", "stop", "pattern", "use", "image"}
SHAPES = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line", "text", "use"}
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
TOKEN = re.compile(rf"[MmZzLlHhVvCcSsQqTtAa]|{NUMBER}")
TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)")


def numbers(value: str) -> list[float]:
    rest = re.sub(NUMBER, "", value)
    if rest.strip(" ,\t\n\r"):
        raise ValueError("Invalid number list")
    result = [float(v) for v in re.findall(NUMBER, value)]
    if not all(math.isfinite(v) and abs(v) <= 1e9 for v in result):
        raise ValueError("Nonfinite or excessive geometry value")
    return result


def parse_transform(value: str) -> None:
    remaining = TRANSFORM.sub("", value)
    if remaining.strip(" ,\t\n\r"):
        raise ValueError("Invalid transform syntax")
    found = list(TRANSFORM.finditer(value))
    if not found:
        raise ValueError("Empty transform")
    for match in found:
        args = numbers(match[2])
        valid = {"matrix": {6}, "translate": {1, 2}, "scale": {1, 2}, "rotate": {1, 3}, "skewX": {1}, "skewY": {1}}
        if len(args) not in valid[match[1]]:
            raise ValueError("Invalid transform arguments")


def path_grammar(d: str) -> None:
    """Check complete command argument groups; permissive path parsers alone are insufficient."""
    tokens = TOKEN.findall(d)
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
    index = 0
    while index < len(tokens):
        command = tokens[index]
        if command.upper() not in arity:
            raise ValueError("A new command is required after closepath")
        index += 1
        start = index
        while index < len(tokens) and tokens[index].upper() not in arity:
            index += 1
        values = tokens[start:index]
        count = arity[command.upper()]
        if (count == 0 and values) or (count and (not values or len(values) % count)):
            raise ValueError("Incomplete path command")
        if command.upper() == "A":
            for i in range(0, len(values), 7):
                if float(values[i]) < 0 or float(values[i + 1]) < 0 or values[i + 3] not in {"0", "1"} or values[i + 4] not in {"0", "1"}:
                    raise ValueError("Invalid arc radius or flags")


def validate_svg(data: bytes | str, *, max_bytes: int = 32 * 1024 * 1024, max_pixels: int = 40_000_000) -> dict:
    if isinstance(data, str):
        data = data.encode()
    errors, warnings = [], []
    report = {"valid_svg": False, "true_vector": False, "illustrator_ready": False,
              "illustrator_compatibility": "FAIL", "status": "INVALID_VECTOR", "embedded_rasters": 0,
              "path_count": 0, "group_count": 0, "text_count": 0, "gradient_count": 0,
              "pattern_count": 0, "clip_path_count": 0, "total_anchor_count": 0,
              "shape_count": 0, "errors": errors, "warnings": warnings,
              "compatibility_scope": "Static SVG feature checks; Adobe Illustrator has not been executed."}
    if len(data) > max_bytes:
        errors.append("SVG exceeds size limit")
        return report
    try:
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("DTD and entities are prohibited")
        root = SafeET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception:
        errors.append("Unsafe or invalid XML")
        return report
    raster = detect(root)
    report.update(raster)
    tags = Counter(e.tag.split("}")[-1] for e in root.iter())
    report.update({"path_count": tags["path"], "group_count": tags["g"], "text_count": tags["text"],
                   "gradient_count": tags["linearGradient"] + tags["radialGradient"],
                   "pattern_count": tags["pattern"], "clip_path_count": tags["clipPath"],
                   "shape_count": sum(tags[s] for s in SHAPES)})
    if root.tag != f"{{{SVG}}}svg":
        errors.append("Root must be an SVG element in the SVG namespace")
    try:
        view = numbers(root.get("viewBox", ""))
        if len(view) != 4 or min(view[2:]) <= 0 or view[2] * view[3] > max_pixels:
            raise ValueError("Invalid viewBox")
        for dimension in ("width", "height"):
            value = root.get(dimension)
            if value is not None:
                match = re.fullmatch(rf"({NUMBER})(px|mm|cm|in|pt|pc)?", value.strip())
                if not match or not math.isfinite(float(match[1])) or float(match[1]) <= 0:
                    raise ValueError("Invalid dimensions")
        report["view_box"] = view
    except ValueError:
        errors.append("Missing/invalid viewBox or dimensions, or excessive rendering area")
    ids = {}
    refs = []
    meaningful = 0
    degenerate = 0
    parent = {child: e for e in root.iter() for child in e}
    for e in root.iter():
        tag = e.tag.split("}")[-1]
        if e.tag != f"{{{SVG}}}{tag}" or tag not in ALLOWED:
            errors.append(f"Unsafe or unsupported element: {tag}")
        identifier = e.get("id")
        if identifier:
            if identifier in ids or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", identifier):
                errors.append("Duplicate or malformed id")
            ids[identifier] = e
        for raw_key, raw_value in e.attrib.items():
            key, value = raw_key.split("}")[-1], decoded(raw_value).strip()
            if key.lower().startswith("on") or key in {"src", "base"}:
                errors.append(f"Unsafe attribute: {key}")
            if any(term in value.lower() for term in ("javascript:", "vbscript:", "@import", "expression(", "-moz-binding")):
                errors.append("Unsafe URI or CSS")
            if key == "href":
                if value.startswith("#"):
                    refs.append((e, value[1:]))
                elif not (tag == "image" and re.match(r"data:image/(png|jpeg|webp);base64,", value, re.I)):
                    errors.append("External resources and unsupported data URIs are prohibited")
            if "url" in value.lower():
                url_matches = list(re.finditer(r"url\s*\(\s*['\"]?([^'\")]+)['\"]?\s*\)", value, re.I))
                if not url_matches:
                    errors.append("Malformed CSS URL")
                for match in url_matches:
                    target = match[1].strip()
                    if not target.startswith("#"):
                        errors.append("External/CSS data resources are prohibited")
                    else:
                        refs.append((e, target[1:]))
            if key in {"transform", "gradientTransform", "patternTransform"}:
                try:
                    parse_transform(value)
                except ValueError:
                    errors.append("Invalid transform")
        try:
            if tag == "path":
                d = e.get("d", "")
                if not d.strip() or not re.match(r"\s*[Mm]", d) or TOKEN.sub("", d).strip(" ,\t\n\r"):
                    raise ValueError("Invalid path tokens")
                numbers(re.sub(r"[MmZzLlHhVvCcSsQqTtAa]", " ", d))
                path_grammar(d)
                path = parse_path(d)
                if not path:
                    raise ValueError("Empty path")
                if all(segment.start == segment.end and all(getattr(segment,control,segment.start) == segment.start for control in ("control","control1","control2")) for segment in path):
                    degenerate += 1
                    raise ValueError("Degenerate path")
                report["total_anchor_count"] += len(path) + len(re.findall("[Mm]", d))
            if tag in {"polygon", "polyline"}:
                points = numbers(e.get("points", ""))
                if len(points) % 2 or len(points) < (6 if tag == "polygon" else 4):
                    raise ValueError("Invalid points")
                report["total_anchor_count"] += len(points) // 2
            for field in ("x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry"):
                if field in e.attrib and not re.fullmatch(rf"{NUMBER}%?", e.get(field, "")):
                    raise ValueError("Invalid shape coordinates")
                if field in e.attrib and not math.isfinite(float(e.get(field).rstrip("%"))):
                    raise ValueError("Nonfinite shape coordinate")
                if field in {"r", "rx", "ry"} and field in e.attrib and float(e.get(field).rstrip("%")) < 0:
                    raise ValueError("Negative radius")
            if tag in {"rect", "image", "pattern", "mask"}:
                for field in ("width", "height"):
                    if field in e.attrib and not re.fullmatch(rf"{NUMBER}(?:%|px|mm|cm|in|pt|pc)?", e.get(field, "")):
                        raise ValueError("Invalid shape dimensions")
            if tag in {"linearGradient", "radialGradient"}:
                stops = [c for c in e if c.tag.split("}")[-1] == "stop"]
                if not stops and not any(k.split("}")[-1] == "href" for k in e.attrib):
                    raise ValueError("Gradient has no stops/reference")
                offsets = []
                for stop in stops:
                    value = stop.get("offset", "")
                    offset = float(value.rstrip("%")) / (100 if value.endswith("%") else 1)
                    if not 0 <= offset <= 1:
                        raise ValueError("Gradient offset out of range")
                    offsets.append(offset)
                if offsets != sorted(offsets):
                    raise ValueError("Gradient offsets are not ordered")
            if tag == "text":
                warnings.append("Editable text requires font substitution review in Illustrator")
            if tag == "mask":
                warnings.append("Mask requires a visual check in Illustrator")
        except (ValueError, AssertionError, IndexError, ZeroDivisionError, TypeError):
            errors.append(f"Invalid {tag} geometry")
        if tag in SHAPES:
            ancestor = e
            hidden = False
            while ancestor is not None:
                if ancestor.tag.split("}")[-1] in {"defs", "clipPath", "mask", "pattern", "metadata"}:
                    hidden = True
                if ancestor.get("display") == "none" or ancestor.get("visibility") == "hidden" or ancestor.get("opacity") == "0":
                    hidden = True
                if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:[;\s]|$))", ancestor.get("style", "")):
                    hidden = True
                ancestor = parent.get(ancestor)
            if e.get("fill") == "none" and e.get("stroke", "none") == "none":
                hidden = True
            # Degenerate geometry cannot establish a meaningful editable artwork.
            try:
                if tag == "path" and not any(segment.start != segment.end or segment.__class__.__name__ in {"CubicBezier", "QuadraticBezier", "Arc"} for segment in parse_path(e.get("d", ""))):
                    hidden = True
                if tag == "rect" and any(float(e.get(k, "0").rstrip("%pxmcint")) <= 0 for k in ("width", "height")):
                    hidden = True
                if tag == "circle" and float(e.get("r", "0").rstrip("%")) <= 0:
                    hidden = True
                if tag == "ellipse" and any(float(e.get(k, "0").rstrip("%")) <= 0 for k in ("rx", "ry")):
                    hidden = True
                if tag == "line" and (e.get("stroke", "none") == "none" or (e.get("x1", "0"), e.get("y1", "0")) == (e.get("x2", "0"), e.get("y2", "0"))):
                    hidden = True
                style = e.get("style", "")
                if re.search(r"fill\s*:\s*none(?:;|$)", style) and not re.search(r"stroke\s*:\s*(?!none)[^;]+", style) and e.get("stroke", "none") == "none":
                    hidden = True
            except (ValueError, AssertionError, IndexError, TypeError):
                hidden = True
            if tag == "text" and not "".join(e.itertext()).strip():
                hidden = True
            if not hidden:
                meaningful += 1
    for e, reference in refs:
        if reference not in ids:
            errors.append(f"Missing reference: {reference}")
        elif e is ids[reference]:
            errors.append("Self-referencing SVG resource")
    # Resource/reference cycles can cause renderer recursion or invisible artwork.
    graph = {identifier: set() for identifier in ids}
    for e, reference in refs:
        ancestor = e
        while ancestor is not None and not ancestor.get("id"):
            ancestor = parent.get(ancestor)
        if ancestor is not None:
            graph[ancestor.get("id")].add(reference)
    visiting, visited = set(), set()
    def walk(node):
        if node in visiting:
            raise ValueError("Reference cycle")
        if node in visited:
            return
        visiting.add(node)
        for child in graph.get(node, []):
            walk(child)
        visiting.remove(node)
        visited.add(node)
    try:
        for node in graph:
            walk(node)
    except (ValueError, RecursionError):
        errors.append("Cyclic SVG resource references")
    report["degenerate_object_count"] = degenerate
    report["meaningful_shape_count"] = meaningful
    report["errors"] = list(dict.fromkeys(errors))
    report["warnings"] = list(dict.fromkeys(warnings))
    report["valid_svg"] = not errors
    has_raster = raster["embedded_rasters"] > 0
    report["true_vector"] = not errors and not has_raster and meaningful > 0
    if report["true_vector"]:
        report["status"] = "TRUE_VECTOR"
    elif not errors and has_raster and meaningful > 0:
        report["status"] = "HYBRID_VECTOR"
    if has_raster:
        report["warnings"].append("Raster artwork detected; fully editable/True Vector export prohibited")
    if not meaningful:
        report["warnings"].append("No meaningful vector artwork outside definitions")
    if not errors and meaningful:
        report["illustrator_compatibility"] = "PASS_WITH_WARNINGS" if report["warnings"] else "PASS"
        report["illustrator_ready"] = report["true_vector"]
    # Aliases used by the JerseyOS contract.
    report.update({"paths": report["path_count"], "groups": report["group_count"],
                   "texts": report["text_count"], "gradients": report["gradient_count"],
                   "patterns": report["pattern_count"], "anchor_points": report["total_anchor_count"]})
    return report
