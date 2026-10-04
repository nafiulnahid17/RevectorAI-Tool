"""Retain truthful eight-part acceptance artifacts; AI mocked, all vector operations real."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from io import BytesIO
import json
import subprocess
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from app.core.config import Settings
from app.core.engine import Engine
from app.ai.router import AIRouter
from app.validators.svg_validator import validate_svg
from tests.integration.test_upgrade import MockArtworkProvider, sheet, confirm
from app.models.project import ProcessingSettings


def main():
    output = Path("samples/upgrade")
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    with TemporaryDirectory(prefix="revector-upgrade-") as folder:
        e = Engine(
            Settings(data_dir=Path(folder)),
            ai_router=AIRouter(primary=MockArtworkProvider()),
        )
        p = e.create(
            name="Eight-piece acceptance fixture",
            settings=ProcessingSettings(
                vector_mode="precision",
                gradients=False,
                mockup_width=1024,
                mockup_height=768,
            ),
        )
        source = BytesIO()
        sheet()[0].save(source, format="PNG")
        e.upload(p.project_id, source.getvalue(), "eight-parts.png")
        e.run(p.project_id, "prepare")
        confirm(e, e.load(p.project_id))
        # Explicit provided dimensions only; sleeves/collars/trims uncalibrated.
        for part in e.load(p.project_id).parts:
            if part.type in {"front_body", "back_body"}:
                e.manual(
                    p.project_id,
                    "update",
                    part.part_id,
                    {
                        "physical_width_mm": 220,
                        "physical_height_mm": 310,
                        "confirmed": True,
                    },
                )
        confirm(e, e.load(p.project_id))
        e.run(p.project_id, "production")
        p = e.load(p.project_id)
        result = e.run(
            p.project_id, "export", {"formats": ["svg", "pdf", "eps", "zip"]}
        )
        assert not result["export_errors"], result
        for part in e.load(p.project_id).parts:
            files = result["part_files"][part.part_id]
            record = {
                "part": part.type,
                "native_adobe_illustrator": "NOT_RUN",
                "files": {},
            }
            for fmt, key in files.items():
                target = output / f"{part.type}.{fmt}"
                target.write_bytes(e.storage.get(key))
                record["files"][fmt] = str(target)
            report = validate_svg((output / f"{part.type}.svg").read_bytes())
            assert report["true_vector"] and report["embedded_rasters"] == 0
            record["vector_validation"] = report
            raw = (output / f"{part.type}.svg").read_bytes()
            root = ET.fromstring(raw)
            shape = next(
                n
                for n in root.iter()
                if n.tag.endswith("path") and n.get("fill") not in {None, "none"}
            )
            original_id = shape.get("id")
            shape.set("fill", "#ff0033")
            assert validate_svg(ET.tostring(root))["true_vector"] and original_id
            query = subprocess.run(
                ["inkscape", str(output / f"{part.type}.svg"), "--query-all"],
                capture_output=True,
                timeout=60,
            )
            assert query.returncode == 0 and original_id.encode() in query.stdout
            record["inkscape_import_editable_objects"] = "PASS"
            record["shape_fill_edit_strict_parse"] = "PASS"
            images = subprocess.run(
                ["pdfimages", "-list", str(output / f"{part.type}.pdf")],
                capture_output=True,
                timeout=30,
            )
            assert (
                images.returncode == 0 and len(images.stdout.decode().splitlines()) == 2
            )
            record["pdf_raster_count"] = 0
            eps = subprocess.run(
                [
                    "gs",
                    "-q",
                    "-dSAFER",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-sDEVICE=nullpage",
                    str(output / f"{part.type}.eps"),
                ],
                capture_output=True,
                timeout=30,
            )
            assert eps.returncode == 0
            record["ghostscript_eps_parse"] = "PASS"
            checks.append(record)
        (output / "parts-only-production.zip").write_bytes(
            e.storage.get(result["exports"]["zip"])
        )
        (output / "validation.json").write_text(json.dumps(p.validation, indent=2))
        (output / "events.json").write_text(json.dumps(p.events, indent=2))
        (output / "acceptance.json").write_text(
            json.dumps(
                {
                    "ai_test_mode": "explicit mocked provider; no live AI calls",
                    "parts": checks,
                    "assembled_download": False,
                    "native_ai_available": False,
                },
                indent=2,
            )
        )
    print(
        json.dumps(
            {
                "parts": len(checks),
                "true_vector": True,
                "embedded_rasters": 0,
                "svg_pdf_eps": "PASS",
                "inkscape_ghostscript": "PASS",
                "adobe_illustrator": "NOT_RUN",
                "directory": str(output),
            }
        )
    )


if __name__ == "__main__":
    main()
