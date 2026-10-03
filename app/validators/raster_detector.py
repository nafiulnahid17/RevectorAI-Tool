"""Detect images and disguised raster resources in attributes, CSS, and decoded URI text."""
import re
from urllib.parse import unquote

RASTER = re.compile(r"data\s*:\s*image/|\.(?:png|jpe?g|webp|gif|bmp|tiff?|avif)(?:[\s?#'\")]|$)", re.I)


def decoded(value: str) -> str:
    # Decode CSS escapes such as da\74 a:image and URL encoding.
    value = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(int(m[1], 16)) if int(m[1], 16) <= 0x10ffff else "", value)
    for _ in range(3):
        next_value = unquote(value)
        if next_value == value:
            break
        value = next_value
    return value


def detect(root) -> dict:
    images = [e for e in root.iter() if e.tag.split("}")[-1] == "image"]
    refs = []
    for e in root.iter():
        for key, value in e.attrib.items():
            # IDs and user metadata containing filenames are not rendering resources.
            if key.split("}")[-1] in {"href", "src", "style", "fill", "stroke", "filter", "mask", "clip-path"}:
                if RASTER.search(decoded(value)):
                    refs.append({"tag": e.tag.split("}")[-1], "attribute": key.split("}")[-1]})
        if e.tag.split("}")[-1] == "style" and RASTER.search(decoded(e.text or "")):
            refs.append({"tag": "style", "attribute": "text"})
    return {"raster_image_count": len(images), "raster_reference_count": len(refs),
            "embedded_rasters": len(images) + sum(r["tag"] != "image" for r in refs), "raster_references": refs}
