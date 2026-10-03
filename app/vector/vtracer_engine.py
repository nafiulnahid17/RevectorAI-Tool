from pathlib import Path
from defusedxml import ElementTree as SafeET
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.vector.presets import PRESETS


def trace(source: Path, destination: Path, settings: ProcessingSettings):
    try:
        import vtracer
    except ImportError as exc:
        raise EngineError("VECTOR_TRACE_FAILED", "VTracer library is unavailable") from exc
    p = PRESETS[settings.preset]
    try:
        vtracer.convert_image_to_svg_py(str(source), str(destination), colormode="color", hierarchical="stacked",
                                       mode="spline", filter_speckle=p["filter_speckle"],
                                       color_precision=p["color_precision"], layer_difference=16,
                                       corner_threshold=60, length_threshold=max(.5, p["epsilon"]),
                                       max_iterations=10, splice_threshold=45, path_precision=p["path_precision"])
        return SafeET.fromstring(destination.read_bytes()), {"backend": "vtracer", "parameters": p}
    except Exception as exc:
        raise EngineError("VECTOR_TRACE_FAILED", "VTracer did not produce a parseable SVG") from exc
