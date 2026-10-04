"""Gated exports, real Inkscape conversion, and production ZIP packaging."""

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

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
            exported = output.read_bytes()
            signature = b"%PDF" if format == "pdf" else b"%!PS"
            if not exported.startswith(signature) or len(exported) < 100:
                raise EngineError(
                    "EXPORT_CONVERSION_FAILED",
                    "Conversion did not produce a valid output signature",
                )
            verify_conversion(output, format, timeout, mode)
            return exported
        except (subprocess.SubprocessError, OSError) as exc:
            raise EngineError(
                "EXPORT_CONVERSION_FAILED",
                "Inkscape conversion failed or timed out; SVG is preserved",
            ) from exc
