"""Native tracing in a killable subprocess; argv is constructed only by the engine."""

from pathlib import Path

from app.vector.presets import PRESETS


def convert(source: Path, destination: Path, preset: str):
    import vtracer

    p = PRESETS[preset]
    vtracer.convert_image_to_svg_py(
        str(source),
        str(destination),
        colormode="color",
        hierarchical="stacked",
        mode="spline",
        filter_speckle=p["filter_speckle"],
        color_precision=p["color_precision"],
        layer_difference=16,
        corner_threshold=60,
        length_threshold=max(0.5, p["epsilon"]),
        max_iterations=10,
        splice_threshold=45,
        path_precision=p["path_precision"],
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4 or sys.argv[3] not in PRESETS:
        raise SystemExit(2)
    try:
        convert(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except Exception:  # noqa: BLE001 — native worker exits without exposing exception data
        # No arbitrary exception/provider/environment values in captured output.
        raise SystemExit(1)
