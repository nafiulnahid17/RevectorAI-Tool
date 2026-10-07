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


def _physical_inches(value: str) -> float | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|in|pt|pc)\s*", value or "")
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2)
    factors = {"mm": 1 / 25.4, "cm": 1 / 2.54, "in": 1.0, "pt": 1 / 72, "pc": 1 / 6}
    return number * factors[unit]


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


def _run_ghostscript_cmyk(
    source_pdf: Path,
    output: Path,
    format: str,
    timeout: int,
) -> None:
    """Produce deterministic CMYK handoff output without rasterizing vector artwork."""
    ghostscript = shutil.which("gs")
    if not ghostscript:
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            "Ghostscript is required for CMYK production PDF/EPS export",
        )

    if format == "eps":
        device_args = [
            "-sDEVICE=eps2write",
            "-dLanguageLevel=2",
            "-dEPSCrop",
        ]
    elif format == "pdf":
        device_args = [
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.6",
        ]
    else:
        raise EngineError("UNSUPPORTED_EXPORT", "CMYK conversion supports PDF/EPS only")

    args = [
        ghostscript,
        "-q",
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        *device_args,
        "-sProcessColorModel=DeviceCMYK",
        "-sColorConversionStrategy=CMYK",
        "-dOverrideICC",
        f"-sOutputFile={output}",
        str(source_pdf),
    ]
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode or not output.exists():
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            f"Ghostscript CMYK {format.upper()} conversion failed; SVG is preserved",
        )


def _verify_eps_level2_profile(path: Path) -> None:
    header = path.read_bytes()[:16384].decode("latin-1", errors="replace")
    first_line = header.splitlines()[0] if header else ""
    if "EPSF-3.0" not in first_line:
        raise EngineError(
            "EXPORT_PROFILE_MISMATCH",
            "EPS output is not EPSF 3.0",
        )
    if "%%LanguageLevel: 2" not in header:
        raise EngineError(
            "EXPORT_PROFILE_MISMATCH",
            "EPS output is not PostScript LanguageLevel 2",
        )
    if "%%BoundingBox:" not in header:
        raise EngineError(
            "EXPORT_PROFILE_MISMATCH",
            "EPS output is missing a production BoundingBox",
        )


def convert(
    data: bytes, format: str, timeout: int = 180, mode: str = "true_vector"
) -> bytes:
    """Export production vector PDF/EPS.

    SVG is first converted to vector PDF with text outlined, then Ghostscript
    rewrites it using DeviceCMYK. EPS additionally targets EPSF 3.0 and
    PostScript LanguageLevel 2. Strict parser checks reject rasterization.
    """
    if format not in {"pdf", "eps"}:
        raise EngineError("UNSUPPORTED_EXPORT", "Production export supports PDF/EPS only")
    approve(data, mode)

    inkscape = shutil.which("inkscape")
    if not inkscape:
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            "Inkscape CLI is unavailable; validated SVG is preserved",
        )

    with TemporaryDirectory(prefix="revector-export-") as temp:
        folder = Path(temp)
        source = folder / "source.svg"
        intermediate = folder / "intermediate.pdf"
        output = folder / f"output.{format}"
        source.write_bytes(data)

        env = os.environ.copy()
        env["INKSCAPE_PROFILE_DIR"] = str(folder / "profile")
        try:
            result = subprocess.run(
                [
                    inkscape,
                    str(source),
                    "--export-type=pdf",
                    f"--export-filename={intermediate}",
                    "--export-text-to-path",
                ],
                check=False,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            if result.returncode or not intermediate.exists():
                raise EngineError(
                    "EXPORT_CONVERSION_FAILED",
                    "Inkscape vector PDF conversion failed; SVG is preserved",
                )

            _run_ghostscript_cmyk(intermediate, output, format, timeout)
            exported = output.read_bytes()
            signature = b"%PDF" if format == "pdf" else b"%!PS"
            if not exported.startswith(signature) or len(exported) < 100:
                raise EngineError(
                    "EXPORT_CONVERSION_FAILED",
                    "Conversion did not produce a valid output signature",
                )
            if format == "eps":
                _verify_eps_level2_profile(output)
            verify_conversion(output, format, timeout, mode)
            return exported
        except EngineError:
            raise
        except (subprocess.SubprocessError, OSError) as exc:
            raise EngineError(
                "EXPORT_CONVERSION_FAILED",
                "Production CMYK conversion failed or timed out; SVG is preserved",
            ) from exc

