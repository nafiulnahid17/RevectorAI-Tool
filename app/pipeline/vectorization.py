from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from xml.etree import ElementTree as ET
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.vector import contour, vtracer_engine, potrace_engine
from app.vector.gradients import fit_linear
from app.validators.svg_validator import validate_svg


def vectorize(reference: Image.Image, source: Image.Image, settings: ProcessingSettings, timeout: int):
    original_size = source.size
    scale = 1.0
    if settings.max_trace_dimension and max(source.size) > settings.max_trace_dimension:
        scale = settings.max_trace_dimension / max(source.size)
        size = tuple(max(2, round(v * scale)) for v in source.size)
        source = source.resize(size, Image.Resampling.LANCZOS)
        reference = reference.resize(size, Image.Resampling.LANCZOS)
    notes = []
    attempts = []
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
                    root, metadata = vtracer_engine.trace(input_path, folder / "trace.svg", settings, timeout=timeout)
                # All native trace engines pass the same grammar/integrity gate.
                for node in list(root.iter()):
                    for child in list(node):
                        if child.tag.split('}')[-1]=='path' and not child.get('d','').strip():
                            node.remove(child)
                check = validate_svg(ET.tostring(root))
                if not check['true_vector']:
                    raise EngineError('INVALID_PATH_GEOMETRY','Native trace failed deterministic geometry validation',diagnostics={'validation_errors':check['errors']})
                attempts.append({'engine':metadata['backend'],'status':'PASS'})
            except EngineError as exc:
                if not settings.allow_contour_fallback:
                    raise
                attempts.append({'engine':'potrace' if settings.vector_mode=='mono' else 'vtracer','status':'FAILED','code':exc.code})
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
    metadata.update({"attempts":attempts,"fallback_attempted":any(a["status"]=="FAILED" for a in attempts),"trace_scale": scale, "source_dimensions": list(original_size), "warnings": notes})
    if not any(e.tag.split("}")[-1] == "path" for e in root.iter()):
        raise EngineError("VECTOR_TRACE_FAILED", "No meaningful vector paths were produced")
    check = validate_svg(ET.tostring(root))
    if not check['true_vector']:
        raise EngineError('INVALID_PATH_GEOMETRY','Vector fallback also failed geometry checks',diagnostics={'validation_errors':check['errors'],'attempts':attempts})
    return root, metadata
