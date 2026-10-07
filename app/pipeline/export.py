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
    source: Path,
    output: Path,
    *,
    format: str,
    timeout: int,
) -> None:
    """Normalize production PDF/EPS to a CMYK, vector-preserving handoff profile."""
    ghostscript = shutil.which("gs")
    if not ghostscript:
        raise EngineError(
            "EXPORT_VALIDATION_UNAVAILABLE",
            "Ghostscript is required for CMYK production PDF/EPS output",
        )
    device = "eps2write" if format == "eps" else "pdfwrite"
    args = [
        ghostscript,
        "-q",
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        f"-sDEVICE={device}",
        "-sProcessColorModel=DeviceCMYK",
        "-sColorConversionStrategy=CMYK",
        "-dOverrideICC",
    ]
    if format == "eps":
        args.extend(["-dLanguageLevel=2", "-dEPSCrop"])
    else:
        args.append("-dCompatibilityLevel=1.4")
    args.extend([f"-sOutputFile={output}", str(source)])
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode or not output.exists():
        raise EngineError(
            "EXPORT_CONVERSION_FAILED",
            f"Ghostscript CMYK {format.upper()} normalization failed; SVG is preserved",
        )


def verify_eps_client_profile(data: bytes) -> None:
    """Enforce the client's CorelDRAW-era EPS interchange profile."""
    header = data[:4096]
    if not data.startswith(b"%!PS-Adobe") or b"EPSF-3.0" not in header:
        raise EngineError(
            "EXPORT_PROFILE_MISMATCH",
            "EPS must declare Adobe EPSF 3.0",
        )
    if not re.search(br"%%LanguageLevel:\s*2(?:\D|$)", data[:20000]):
        raise EngineError(
            "EXPORT_PROFILE_MISMATCH",
            "EPS must be PostScript Language Level 2",
        )
    if b"%%BoundingBox:" not in header and b"%%HiResBoundingBox:" not in data[:20000]:
        raise EngineError(
            "EXPORT_PROFILE_MISMATCH",
            "EPS is missing a production bounding box",
        )
    if b"setcmykcolor" not in data and b"DeviceCMYK" not in data:
        raise EngineError(
            "EXPORT_PROFILE_MISMATCH",
            "EPS does not expose CMYK color operators",
        )


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
        source = folder / "source.svg"
        raw = folder / f"inkscape-raw.{format}"
        output = folder / f"output.{format}"
        source.write_bytes(data)
        env = os.environ.copy()
        env["INKSCAPE_PROFILE_DIR"] = str(folder / "profile")
        args = [
            executable,
            str(source),
            f"--export-type={format}",
            f"--export-filename={raw}",
            "--export-text-to-path",
        ]
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            if result.returncode or not raw.exists():
                raise EngineError(
                    "EXPORT_CONVERSION_FAILED",
                    f"Inkscape {format.upper()} conversion failed; SVG is preserved",
                )

            _run_ghostscript_cmyk(
                raw,
                output,
                format=format,
                timeout=timeout,
            )
            exported = output.read_bytes()
            signature = b"%PDF" if format == "pdf" else b"%!PS"
            if not exported.startswith(signature) or len(exported) < 100:
                raise EngineError(
                    "EXPORT_CONVERSION_FAILED",
                    "Conversion did not produce a valid output signature",
                )
            if format == "eps":
                verify_eps_client_profile(exported)
            verify_conversion(output, format, timeout, mode)
            return exported
        except EngineError:
            raise
        except (subprocess.SubprocessError, OSError) as exc:
            raise EngineError(
                "EXPORT_CONVERSION_FAILED",
                "Production conversion failed or timed out; SVG is preserved",
            ) from exc

