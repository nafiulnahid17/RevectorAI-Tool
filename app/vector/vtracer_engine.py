from pathlib import Path
import subprocess
import sys
from defusedxml import ElementTree as SafeET
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.vector.presets import PRESETS
from app.validators.svg_validator import validate_svg
from xml.etree import ElementTree as ET


def trace(source: Path, destination: Path, settings: ProcessingSettings, timeout: int | None = None):
    try:
        import vtracer
    except ImportError as exc:
        raise EngineError("VECTOR_TRACE_FAILED", "VTracer library is unavailable") from exc
    p = PRESETS[settings.preset]
    try:
        if timeout is None:
            from app.vector.vtracer_worker import convert
            convert(source,destination,settings.preset)
        else:
            subprocess.run([sys.executable,'-m','app.vector.vtracer_worker',str(source),str(destination),settings.preset],
                           check=True,capture_output=True,timeout=timeout)
        if destination.stat().st_size > 32*1024*1024:
            raise EngineError('VECTOR_TRACE_FAILED','VTracer output exceeds SVG size limit')
        root = SafeET.fromstring(destination.read_bytes())
        if "viewBox" not in root.attrib:
            root.set("viewBox", f"0 0 {root.get('width', '0')} {root.get('height', '0')}")
        # VTracer sometimes writes empty placeholders for collapsed color regions.
        # They draw nothing, but must not reach the strict production validator.
        discarded = 0
        for parent in root.iter():
            for child in list(parent):
                if child.tag.split("}")[-1] == "path" and not child.get("d", "").strip():
                    parent.remove(child)
                    discarded += 1
        report = validate_svg(ET.tostring(root, encoding="utf-8"))
        if not report["true_vector"]:
            raise EngineError("INVALID_PATH_GEOMETRY", "VTracer produced invalid vector geometry; deterministic fallback may be used",diagnostics={"validation_errors":report["errors"],"trace_engine":"vtracer"})
        return root, {"backend": "vtracer", "parameters": p, "hierarchy": "cutout", "pathfinder_compatible": True, "discarded_empty_paths": discarded}
    except subprocess.TimeoutExpired as exc:
        raise EngineError("VECTOR_TRACE_FAILED","VTracer exceeded its subprocess time budget; deterministic fallback may be used") from exc
    except EngineError:
        raise
    except Exception as exc:
        raise EngineError("VECTOR_TRACE_FAILED", "VTracer did not produce a parseable SVG") from exc
