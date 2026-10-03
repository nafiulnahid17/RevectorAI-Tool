"""Gated exports, real Inkscape conversion, and production ZIP packaging."""
from pathlib import Path
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
from app.core.exceptions import EngineError
from app.validators.svg_validator import validate_svg
from app.validators.visual_diff import render_svg


def approve(data: bytes, mode: str = "true_vector") -> dict:
    report = validate_svg(data)
    if not report["valid_svg"] or report["illustrator_compatibility"] == "FAIL":
        raise EngineError("SVG_VALIDATION_FAILED", "Export blocked by SVG integrity/compatibility checks")
    if mode == "true_vector" and report["embedded_rasters"]:
        raise EngineError("RASTER_FOUND_IN_TRUE_VECTOR", "True Vector export contains a raster layer")
    if report["meaningful_shape_count"] == 0:
        raise EngineError("SVG_VALIDATION_FAILED", "Export contains no meaningful vector artwork")
    render_svg(data)
    return report


def convert(data: bytes, format: str, timeout: int = 180, mode: str = "true_vector") -> bytes:
    if format not in {"pdf", "eps"}:
        raise EngineError("UNSUPPORTED_EXPORT", "Inkscape export supports PDF/EPS only")
    approve(data, mode)
    executable = shutil.which("inkscape")
    if not executable:
        raise EngineError("EXPORT_CONVERSION_FAILED", "Inkscape CLI is unavailable; validated SVG is preserved")
    with TemporaryDirectory(prefix="revector-export-") as temp:
        folder = Path(temp)
        source, output = folder / "source.svg", folder / f"output.{format}"
        source.write_bytes(data)
        env = os.environ.copy()
        env["INKSCAPE_PROFILE_DIR"] = str(folder / "profile")
        args = [executable, str(source), f"--export-type={format}", f"--export-filename={output}", "--export-text-to-path"]
        try:
            result = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
            if result.returncode or not output.exists():
                raise EngineError("EXPORT_CONVERSION_FAILED", f"Inkscape {format.upper()} conversion failed; SVG is preserved")
            exported = output.read_bytes()
            signature = b"%PDF" if format == "pdf" else b"%!PS"
            if not exported.startswith(signature) or len(exported) < 100:
                raise EngineError("EXPORT_CONVERSION_FAILED", "Conversion did not produce a valid output signature")
            return exported
        except (subprocess.SubprocessError, OSError) as exc:
            raise EngineError("EXPORT_CONVERSION_FAILED", "Inkscape conversion failed or timed out; SVG is preserved") from exc
