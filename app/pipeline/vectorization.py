from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from xml.etree import ElementTree as ET
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.vector import contour, vtracer_engine, potrace_engine
from app.vector.gradients import fit_linear


def vectorize(reference: Image.Image, source: Image.Image, settings: ProcessingSettings, timeout: int):
    original_size = source.size
    scale = 1.0
    if settings.max_trace_dimension and max(source.size) > settings.max_trace_dimension:
        scale = settings.max_trace_dimension / max(source.size)
        size = tuple(max(2, round(v * scale)) for v in source.size)
        source = source.resize(size, Image.Resampling.LANCZOS)
        reference = reference.resize(size, Image.Resampling.LANCZOS)
    notes = []
    fitted = fit_linear(reference) if settings.gradients and settings.vector_mode != "mono" else None
    if fitted:
        root, metadata = fitted
    elif settings.vector_mode in {"precision", "reconstruction"}:
        root, metadata = contour.trace(source, settings)
    else:
        with TemporaryDirectory(prefix="revector-trace-") as temp:
            folder = Path(temp)
            try:
                if settings.vector_mode == "mono":
                    root, metadata = potrace_engine.trace(reference, folder, settings, timeout)
                else:
                    input_path = folder / "source.png"
                    source.save(input_path)
                    root, metadata = vtracer_engine.trace(input_path, folder / "trace.svg", settings)
            except EngineError as exc:
                if not settings.allow_contour_fallback:
                    raise
                notes.append(f"{exc.code}: {exc.message}; deterministic contour fallback used.")
                root, metadata = contour.trace(source, settings, mono=settings.vector_mode == "mono")
    if scale != 1:
        group = ET.Element(f"{{{contour.SVG}}}g", {"transform": f"scale({1 / scale})"})
        for child in list(root):
            root.remove(child)
            group.append(child)
        root.append(group)
    root.set("viewBox", f"0 0 {original_size[0]} {original_size[1]}")
    root.set("width", str(original_size[0]))
    root.set("height", str(original_size[1]))
    metadata.update({"trace_scale": scale, "source_dimensions": list(original_size), "warnings": notes})
    if not any(e.tag.split("}")[-1] == "path" for e in root.iter()):
        raise EngineError("VECTOR_TRACE_FAILED", "No meaningful vector paths were produced")
    return root, metadata
