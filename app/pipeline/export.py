"""Gated exports, real Inkscape conversion, and production ZIP packaging."""

import os
import re
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from defusedxml import ElementTree as SafeET
from PIL import Image

from app.core.exceptions import EngineError
from app.validators.svg_validator import validate_svg
from app.validators.visual_diff import render_svg


def approve(data: bytes, mode: str = "true_vector") -> dict:
    report = validate_svg(data)
    if not report["valid_svg"] or report["illustrator_compatibility"] == "FAIL":
        raise EngineError(
            "SVG_VALIDATION_FAILED",
            "Export blocked by SVG integrity/compatibility checks",
        )
    if mode == "true_vector" and report["embedded_rasters"]:
        raise EngineError(
            "RASTER_FOUND_IN_TRUE_VECTOR", "True Vector export contains a raster layer"
        )
    if report["meaningful_shape_count"] == 0:
        raise EngineError(
            "SVG_VALIDATION_FAILED", "Export contains no meaningful vector artwork"
        )
    render_svg(data)
    return report


def verify_conversion(path: Path, format: str, timeout: int, mode: str) -> None:
    """Parse exported documents and block conversion-time rasterization in True Vector mode."""
    pdfinfo, pdfimages = shutil.which("pdfinfo"), shutil.which("pdfimages")
    ghostscript = shutil.which("gs")
    if not pdfinfo or not pdfimages or (format == "eps" and not ghostscript):
        raise EngineError(
            "EXPORT_VALIDATION_UNAVAILABLE",
            "Strict PDF/EPS validation requires Poppler tools and EPS also requires Ghostscript; validated SVG is preserved",
        )
    pdf = path
    try:
        if format == "eps":
            pdf = path.with_name("eps-integrity-check.pdf")
            subprocess.run(
                [
                    ghostscript,
                    "-q",
                    "-dSAFER",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-sDEVICE=pdfwrite",
                    f"-sOutputFile={pdf}",
                    str(path),
                ],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        subprocess.run(
            [pdfinfo, str(pdf)], check=True, capture_output=True, timeout=timeout
        )
        result = subprocess.run(
            [pdfimages, "-list", str(pdf)],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        image_rows = [
            line
            for line in result.stdout.decode("utf-8", errors="replace").splitlines()
            if line.split() and line.split()[0].isdigit()
        ]
        if mode == "true_vector" and image_rows:
            raise EngineError(
                "RASTER_FOUND_IN_TRUE_VECTOR",
                "Document conversion introduced raster objects; export blocked and validated SVG preserved",
            )
    except (subprocess.SubprocessError, OSError) as exc:
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            "Exported document failed strict parser checks or timed out; SVG is preserved",
        ) from exc



def _normalize_print_color(path: Path, format: str, timeout: int) -> None:
    """Normalize vector print documents through Ghostscript.

    EPS is emitted as EPSF 3.0 / PostScript LanguageLevel 2 in DeviceCMYK.
    PDF is normalized to a vector-preserving DeviceCMYK print document.
    Strict raster checks run afterwards, so a conversion that flattens artwork
    is rejected rather than silently shipped.
    """
    ghostscript = shutil.which("gs")
    if not ghostscript:
        raise EngineError(
            "EXPORT_VALIDATION_UNAVAILABLE",
            "CMYK print normalization requires Ghostscript",
        )
    normalized = path.with_name("normalized-" + path.name)
    common = [
        ghostscript,
        "-q",
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        "-dAutoRotatePages=/None",
        "-sProcessColorModel=DeviceCMYK",
        "-sColorConversionStrategy=CMYK",
        "-dOverrideICC",
    ]
    if format == "eps":
        args = common + [
            "-sDEVICE=eps2write",
            "-dLanguageLevel=2",
            "-dEPSCrop",
            f"-sOutputFile={normalized}",
            str(path),
        ]
    elif format == "pdf":
        args = common + [
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-sOutputFile={normalized}",
            str(path),
        ]
    else:
        raise EngineError("UNSUPPORTED_EXPORT", "CMYK normalization supports PDF/EPS only")
    try:
        subprocess.run(
            args,
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            f"{format.upper()} CMYK normalization failed; validated SVG is preserved",
        ) from exc
    if not normalized.exists() or normalized.stat().st_size < 100:
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            f"{format.upper()} CMYK normalization produced no usable file",
        )
    payload = normalized.read_bytes()
    if format == "eps":
        header = payload[:4096].decode("latin-1", errors="replace")
        if not header.startswith("%!PS-Adobe-3.0 EPSF-3.0"):
            raise EngineError(
                "EXPORT_STANDARD_MISMATCH",
                "EPS output is not EPSF 3.0",
            )
        if "%%LanguageLevel: 3" in header:
            raise EngineError(
                "EXPORT_STANDARD_MISMATCH",
                "EPS output exceeded PostScript Level 2",
            )
    path.write_bytes(payload)


def _eps_dsc(data: bytes) -> dict:
    header = data[:8192].decode("latin-1", errors="replace")
    bbox = re.search(
        r"^%%BoundingBox:\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)",
        header,
        re.MULTILINE,
    )
    hires = re.search(
        r"^%%HiResBoundingBox:\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)",
        header,
        re.MULTILINE,
    )
    return {
        "epsf": "3.0" if header.startswith("%!PS-Adobe-3.0 EPSF-3.0") else None,
        "postscript_level": 2 if "%%LanguageLevel: 3" not in header else 3,
        "bounding_box_pt": [int(v) for v in bbox.groups()] if bbox else None,
        "hires_bounding_box_pt": [float(v) for v in hires.groups()] if hires else None,
    }


def output_profile(data: bytes, format: str) -> dict:
    """Public handoff facts for the generated file; never claims Corel authorship."""
    base = {
        "format": format,
        "resolution_independent": format in {"svg", "pdf", "eps"},
        "embedded_raster_policy": "none" if format in {"svg", "pdf", "eps"} else "300_dpi_proof",
    }
    if format == "eps":
        return {
            **base,
            **_eps_dsc(data),
            "color_mode": "CMYK",
            "coreldraw_x8_target": "compatible_interchange",
            "created_by": "ReVector",
        }
    if format == "pdf":
        return {
            **base,
            "color_mode": "CMYK",
            "created_by": "ReVector",
        }
    if format == "svg":
        return {
            **base,
            "color_mode": "RGB_vector_interchange",
            "created_by": "ReVector",
        }
    if format == "png":
        return {
            **base,
            "color_mode": "RGBA",
            "dpi": 300,
            "created_by": "ReVector",
        }
    return base

def _physical_inches(value: str) -> float | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|in|pt|pc)\s*", value or "")
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2)
    factors = {"mm": 1 / 25.4, "cm": 1 / 2.54, "in": 1.0, "pt": 1 / 72, "pc": 1 / 6}
    return number * factors[unit]


def _svg_physical_points(data: bytes) -> tuple[float, float] | None:
    root = SafeET.fromstring(data)
    width_in = _physical_inches(root.get("width", ""))
    height_in = _physical_inches(root.get("height", ""))
    if not width_in or not height_in:
        return None
    return width_in * 72.0, height_in * 72.0


def _validate_eps_page_size(data: bytes, eps_data: bytes, tolerance_pt: float = 1.5) -> dict:
    expected = _svg_physical_points(data)
    if expected is None:
        raise EngineError(
            "EXPORT_DIMENSIONS_REQUIRED",
            "EPS production output requires physical SVG width and height units",
        )
    profile = _eps_dsc(eps_data)
    box = profile.get("hires_bounding_box_pt") or profile.get("bounding_box_pt")
    if not box:
        raise EngineError(
            "EXPORT_STANDARD_MISMATCH",
            "EPS output is missing BoundingBox metadata",
        )
    actual = (float(box[2]) - float(box[0]), float(box[3]) - float(box[1]))
    if (
        abs(actual[0] - expected[0]) > tolerance_pt
        or abs(actual[1] - expected[1]) > tolerance_pt
    ):
        raise EngineError(
            "EXPORT_DIMENSION_MISMATCH",
            "EPS BoundingBox does not match the confirmed physical part size",
            diagnostics={
                "expected_points": [round(expected[0], 3), round(expected[1], 3)],
                "actual_points": [round(actual[0], 3), round(actual[1], 3)],
                "tolerance_points": tolerance_pt,
            },
        )
    return {
        **profile,
        "expected_points": [round(expected[0], 3), round(expected[1], 3)],
        "actual_points": [round(actual[0], 3), round(actual[1], 3)],
        "physical_size_match": True,
    }


def rasterize_png(
    data: bytes,
    *,
    dpi: int = 300,
    timeout: int = 180,
    mode: str = "true_vector",
    max_pixels: int = 80_000_000,
) -> bytes:
    """Render a physically sized SVG to a print-proof PNG at an explicit DPI."""
    approve(data, mode)
    root = SafeET.fromstring(data)
    width_in = _physical_inches(root.get("width", ""))
    height_in = _physical_inches(root.get("height", ""))
    if not width_in or not height_in:
        raise EngineError(
            "EXPORT_DIMENSIONS_REQUIRED",
            "300 DPI PNG requires physical SVG width and height units",
        )
    expected = (max(1, round(width_in * dpi)), max(1, round(height_in * dpi)))
    if expected[0] * expected[1] > max_pixels:
        raise EngineError(
            "EXPORT_PIXEL_LIMIT",
            "300 DPI export exceeds the configured print-render pixel budget",
            diagnostics={"expected_dimensions": list(expected), "dpi": dpi, "max_pixels": max_pixels},
        )
    executable = shutil.which("inkscape")
    if not executable:
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            "Inkscape CLI is unavailable; 300 DPI PNG cannot be generated",
        )
    with TemporaryDirectory(prefix="revector-png-") as temp:
        folder = Path(temp)
        source, output = folder / "source.svg", folder / "output.png"
        source.write_bytes(data)
        env = os.environ.copy()
        env["INKSCAPE_PROFILE_DIR"] = str(folder / "profile")
        result = subprocess.run(
            [
                executable,
                str(source),
                "--export-type=png",
                f"--export-filename={output}",
                f"--export-dpi={dpi}",
                "--export-background-opacity=0",
            ],
            check=False,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode or not output.exists():
            raise EngineError(
                "EXPORT_CONVERSION_FAILED",
                "Inkscape 300 DPI PNG conversion failed; vector files are preserved",
            )
        exported = output.read_bytes()
        if not exported.startswith(b"\x89PNG\r\n\x1a\n"):
            raise EngineError("EXPORT_CONVERSION_FAILED", "PNG conversion produced an invalid signature")
        try:
            with Image.open(BytesIO(exported)) as image:
                actual = image.size
        except Exception as exc:
            raise EngineError("EXPORT_CONVERSION_FAILED", "Generated PNG could not be inspected") from exc
        if actual != expected:
            raise EngineError(
                "EXPORT_DIMENSION_MISMATCH",
                "300 DPI PNG dimensions do not match the physical vector size",
                diagnostics={"expected_dimensions": list(expected), "actual_dimensions": list(actual), "dpi": dpi},
            )
        return exported


def convert(
    data: bytes, format: str, timeout: int = 180, mode: str = "true_vector"
) -> bytes:
    if format not in {"pdf", "eps"}:
        raise EngineError("UNSUPPORTED_EXPORT", "Inkscape export supports PDF/EPS only")
    approve(data, mode)
    executable = shutil.which("inkscape")
    if not executable:
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            "Inkscape CLI is unavailable; validated SVG is preserved",
        )
    with TemporaryDirectory(prefix="revector-export-") as temp:
        folder = Path(temp)
        source, output = folder / "source.svg", folder / f"output.{format}"
        source.write_bytes(data)
        env = os.environ.copy()
        env["INKSCAPE_PROFILE_DIR"] = str(folder / "profile")
        args = [
            executable,
            str(source),
            f"--export-type={format}",
            f"--export-filename={output}",
            "--export-text-to-path",
            "--export-area-page",
        ]
        try:
            result = subprocess.run(
                args, check=False, capture_output=True, timeout=timeout, env=env
            )
            if result.returncode or not output.exists():
                raise EngineError(
                    "EXPORT_CONVERSION_FAILED",
                    f"Inkscape {format.upper()} conversion failed; SVG is preserved",
                )
            _normalize_print_color(output, format, timeout)
            exported = output.read_bytes()
            signature = b"%PDF" if format == "pdf" else b"%!PS"
            if not exported.startswith(signature) or len(exported) < 100:
                raise EngineError(
                    "EXPORT_CONVERSION_FAILED",
                    "Conversion did not produce a valid output signature",
                )
            verify_conversion(output, format, timeout, mode)
            if format == "eps":
                profile = _validate_eps_page_size(data, exported)
                if profile.get("epsf") != "3.0" or profile.get("postscript_level") != 2:
                    raise EngineError(
                        "EXPORT_STANDARD_MISMATCH",
                        "EPS must be EPSF 3.0 / PostScript Level 2",
                    )
            return exported
        except (subprocess.SubprocessError, OSError) as exc:
            raise EngineError(
                "EXPORT_CONVERSION_FAILED",
                "Inkscape conversion failed or timed out; SVG is preserved",
            ) from exc
