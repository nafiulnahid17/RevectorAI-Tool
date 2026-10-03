"""Execute the user-supplied screenshot through a recorded manual front-panel crop."""
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
from PIL import Image
from app.core.config import Settings, capabilities
from app.core.engine import Engine, STAGES
from app.models.project import ProcessingSettings
from app.validators.svg_validator import validate_svg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("samples/attached-front"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source_output = args.output.parent / "attached-input.jpg"
    if args.input.resolve() != source_output.resolve():
        shutil.copyfile(args.input, source_output)
    engine = Engine(Settings(data_dir=Path("data/sample-verification"), sync_jobs=True))
    p = engine.create(name="Attached screenshot – recorded front artwork crop",
                      settings=ProcessingSettings(vector_mode="color", preset="BALANCED", max_colors=16))
    engine.upload(p.project_id, args.input.read_bytes(), args.input.name)
    corners = [[333, 144], [595, 144], [595, 517], [333, 517]]
    (args.output / "corners.json").write_text(json.dumps(corners, indent=2))
    for stage in STAGES:
        params = {}
        if stage == "correct-geometry":
            params = {"corners": corners}
        if stage == "segment":
            current = engine.load(p.project_id)
            w, h = current.geometry["output_dimensions"]
            parts = [{"name": "Front artwork (manually selected from UI screenshot)", "type": "front_body", "confirmed": True,
                      "polygon": [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]}]
            params = {"parts": parts}
            (args.output / "parts.json").write_text(json.dumps(parts, indent=2))
        result = engine.run(p.project_id, stage, params)
        print(json.dumps({"stage": stage, "status": result["status"]}), flush=True)
    export_result = engine.run(p.project_id, "export", {"formats": ["svg", "pdf", "eps", "png", "zip"]})
    p = engine.load(p.project_id)
    for format, key in p.exports.items():
        output = args.output / ("production-pack.zip" if format == "zip" else f"master.{format}")
        output.write_bytes(engine.storage.get(key))
    for name, key in p.previews.items():
        (args.output / (name + Path(key).suffix)).write_bytes(engine.storage.get(key))
    (args.output / "project.json").write_text(p.model_dump_json(indent=2))
    (args.output / "validation.json").write_text(json.dumps(p.validation, indent=2))
    source = engine.storage.get(p.master_svg)
    assert b"<image" not in source and b"data:image/" not in source
    report = validate_svg(source)
    assert report["true_vector"] and report["path_count"] > 0 and report["group_count"] > 0
    assert p.true_vector_ready and p.validation["render_succeeded"]
    verification = {
        "input_file": source_output.name, "input_sha256": p.source_hash,
        "input_dimensions": p.source_metadata["normalized_dimensions"], "manual_corners": corners,
        "sample_scope": "Manually cropped and labeled front artwork inside the supplied UI screenshot; not automatic seven-part detection.",
        "detected_parts": [{"part_id": part.part_id, "type": part.type, "confirmed": part.confirmed,
                            "bbox": part.bbox, "backend": part.metrics["backend"]} for part in p.parts],
        "vector_paths": report["path_count"], "groups": report["group_count"], "anchor_count": report["total_anchor_count"],
        "embedded_rasters": report["embedded_rasters"], "validation_status": report["status"],
        "illustrator_compatibility": p.validation["illustrator_compatibility"],
        "compatibility_scope": report["compatibility_scope"], "render_succeeded": p.validation["render_succeeded"],
        "visual_metrics": p.validation["visual_comparison"], "warnings": p.validation["warnings"],
        "generated_export_files": [Path(key).name for key in p.exports.values()],
        "export_errors": export_result["export_errors"], "dependencies": capabilities(),
        "timings_ms": {stage: metadata["duration_ms"] for stage, metadata in p.stage_metadata.items() if "duration_ms" in metadata},
    }
    (args.output / "verification.json").write_text(json.dumps(verification, indent=2))
    # The deliberate negative fixture is explicitly labeled rejected, never exported as a vector.
    png = BytesIO(); Image.new("RGB", (20, 20), "purple").save(png, "PNG")
    fake = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
            '<image width="20" height="20" href="data:image/png;base64,' +
            base64.b64encode(png.getvalue()).decode() + '"/></svg>')
    (args.output.parent / "rejected-fake.svg").write_text(fake)
    rejected = validate_svg(fake)
    assert not rejected["true_vector"] and rejected["embedded_rasters"] == 1
    (args.output.parent / "rejected-fake.validation.json").write_text(json.dumps(rejected, indent=2))
    print(json.dumps(verification, indent=2))
    return 1 if export_result["export_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
