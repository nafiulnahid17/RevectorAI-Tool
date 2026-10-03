"""Strict raster ingest, EXIF normalization, and preserved source resolution."""
from io import BytesIO
import hashlib
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from app.core.exceptions import EngineError

FORMATS = {"JPEG": ("image/jpeg", {".jpg", ".jpeg"}), "PNG": ("image/png", {".png"}),
           "WEBP": ("image/webp", {".webp"})}


def ingest(data: bytes, filename: str, mime: str | None, max_bytes: int, max_pixels: int) -> tuple[Image.Image, dict]:
    from pathlib import PurePath
    if len(data) > max_bytes:
        raise EngineError("UPLOAD_TOO_LARGE", "File exceeds configured byte limit", status=413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                fmt = probe.format
                if fmt not in FORMATS:
                    raise EngineError("UNSUPPORTED_IMAGE", "Only JPEG, PNG and WEBP are supported", status=415)
                expected_mime, extensions = FORMATS[fmt]
                if PurePath(filename).suffix.lower() not in extensions:
                    raise EngineError("FILE_TYPE_MISMATCH", "Filename extension disagrees with decoded content", status=415)
                if mime and mime not in {expected_mime, "application/octet-stream"}:
                    raise EngineError("MIME_MISMATCH", "MIME type disagrees with decoded content", status=415)
                width, height = probe.size
                if width * height > max_pixels or min(width, height) < 2:
                    raise EngineError("INVALID_DIMENSIONS", "Image dimensions exceed limits or are too small")
                if getattr(probe, "n_frames", 1) != 1:
                    raise EngineError("ANIMATED_IMAGE_UNSUPPORTED", "Supply a single still image")
                probe.verify()
            with Image.open(BytesIO(data)) as source:
                orientation = source.getexif().get(274, 1)
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                normalized.load()
                icc_present = bool(source.info.get("icc_profile"))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise EngineError("CORRUPT_IMAGE", "Image could not be safely decoded") from exc
    return normalized, {
        "format": fmt, "mime": expected_mime, "original_dimensions": [width, height],
        "normalized_dimensions": list(normalized.size), "exif_orientation": orientation,
        "exif_orientation_applied": orientation != 1, "icc_profile_present": icc_present,
        "color_space": "RGB working values; ICC conversion not performed",
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }


def png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
