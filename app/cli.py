"""Debug CLI and reproducible end-to-end processing."""
import argparse
import json
from pathlib import Path
import sys
from app.core.config import Settings
from app.core.engine import Engine, STAGES
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.validators.svg_validator import validate_svg
from app.pipeline.export import convert, approve
from app.validators.visual_diff import render_svg
from app.pipeline.ingest import png_bytes


def main():
    parser = argparse.ArgumentParser(prog="revector", description="Raster → real vector geometry. No raster SVG wrappers.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("analyze", "segment", "vectorize", "run"):
        p = sub.add_parser(command)
        p.add_argument("input", type=Path)
        p.add_argument("--mode", choices=["fast", "balanced", "precision", "ultra"], default="balanced", help="Quality preset")
        p.add_argument("--strategy", choices=["precision", "color", "mono", "reconstruction"], default="color")
        p.add_argument("--corners", type=Path, help="JSON list of four corners for explicit homography/crop")
        p.add_argument("--parts", type=Path, help="JSON list of manual polygons in corrected-image coordinates")
        p.add_argument("--formats", nargs="+", choices=["svg", "pdf", "eps", "png", "zip"], default=["svg", "png", "zip"])
        p.add_argument("--output", type=Path)
    validate = sub.add_parser("validate")
    validate.add_argument("input", type=Path)
    validate.add_argument("--render", action="store_true")
    export = sub.add_parser("export")
    export.add_argument("input", type=Path)
    export.add_argument("--format", choices=["svg", "eps", "pdf", "png"], required=True)
    export.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            report = validate_svg(args.input.read_bytes())
            if args.render and report["valid_svg"]:
                render_svg(args.input.read_bytes())
                report["render_succeeded"] = True
            print(json.dumps(report, indent=2))
            return 0 if report["true_vector"] else 1
        if args.command == "export":
            data = args.input.read_bytes()
            approve(data)
            output = args.output or args.input.with_suffix("." + args.format)
            result = convert(data, args.format) if args.format in {"pdf", "eps"} else (png_bytes(render_svg(data)) if args.format == "png" else data)
            output.write_bytes(result)
            print(json.dumps({"file": str(output), "format": args.format}))
            return 0
        engine = Engine(Settings(data_dir=args.data_dir, sync_jobs=True))
        p = engine.create(name=args.input.stem, settings=ProcessingSettings(preset=args.mode.upper(), vector_mode=args.strategy))
        engine.upload(p.project_id, args.input.read_bytes(), args.input.name)
        last = {"analyze": "analyze", "segment": "segment", "vectorize": "validate", "run": "validate"}[args.command]
        for stage in STAGES[:STAGES.index(last) + 1]:
            params = {}
            if stage == "correct-geometry" and args.corners:
                params["corners"] = json.loads(args.corners.read_text())
            if stage == "segment" and args.parts:
                params["parts"] = json.loads(args.parts.read_text())
            engine.run(p.project_id, stage, params)
        export_result = None
        if last == "validate":
            export_result = engine.run(p.project_id, "export", {"formats": args.formats})
        p = engine.load(p.project_id)
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            for part in p.parts:
                for format,key in (export_result or {}).get('part_files',{}).get(part.part_id,{}).items():
                    target=args.output/'parts'/f'{part.type}-{part.part_id}.{format}'
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(engine.storage.get(key))
                if part.clean_reference:
                    target=args.output/'previews'/f'{part.part_id}-reference.png'
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(engine.storage.get(part.clean_reference))
            zip_key=(export_result or {}).get('exports',{}).get('zip')
            if zip_key:
                (args.output/'parts-only-production.zip').write_bytes(engine.storage.get(zip_key))
            (args.output / "project.json").write_text(p.model_dump_json(indent=2,exclude={'assistant_sessions','master_svg'}))
            if p.validation:
                (args.output / "validation.json").write_text(json.dumps(p.validation, indent=2))
        print(json.dumps({"project_id": p.project_id, "state": p.state, "true_vector_ready": p.true_vector_ready,
                          "parts": [{"id": x.part_id, "type": x.type, "metrics": x.metrics} for x in p.parts],
                          "validation": p.validation, "analysis": p.analysis if args.command == "analyze" else None,
                          "exports": p.exports, "export_errors": (export_result or {}).get("export_errors", {})}, indent=2))
        return 1 if export_result and export_result.get("export_errors") else 0
    except EngineError as exc:
        print(json.dumps({"success": False, "error": exc.as_dict()}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
