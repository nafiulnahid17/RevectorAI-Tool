import hashlib
from io import BytesIO
import json
import shutil
import zipfile
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
from app.core.engine import STAGES
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings, State
from tests.conftest import FIXTURES


def complete(engine, filename="simple_2_color.png", strategy="precision"):
    p = engine.create(name=filename, settings=ProcessingSettings(vector_mode=strategy))
    engine.upload(p.project_id, (FIXTURES / filename).read_bytes(), filename)
    for stage in STAGES:
        engine.run(p.project_id, stage)
    return engine.load(p.project_id)


@pytest.mark.parametrize("filename", list(json.loads((FIXTURES / "expectations.json").read_text())))
def test_imperfect_golden_inputs(engine, filename):
    p = complete(engine, filename)
    assert p.state == State.READY and p.true_vector_ready
    assert p.validation["embedded_rasters"] == 0
    assert p.validation["render_succeeded"]
    assert p.validation["path_count"] > 0
    expected = json.loads((FIXTURES / "expectations.json").read_text())[filename]
    assert len(p.parts) >= expected.get("min_parts", 1)
    assert all(part.type == "unknown" for part in p.parts)
    data = engine.storage.get(p.master_svg)
    assert b"<image" not in data and b"data:image/" not in data


def test_vtracer_produces_geometry(engine):
    pytest.importorskip("vtracer")
    p = complete(engine, "splatter.png", "color")
    assert all(part.metrics["backend"] == "vtracer" for part in p.parts)
    assert p.true_vector_ready


def test_mono_fallback_explicit(engine):
    p = complete(engine, "logo.png", "mono")
    assert p.true_vector_ready
    if not shutil.which("potrace"):
        assert any("Potrace" in warning for part in p.parts for warning in part.warnings)


def test_api_staged_workflow(tmp_path, simple_bytes):
    with TestClient(create_app(Settings(data_dir=tmp_path, sync_jobs=True))) as client:
        assert client.get("/health").json()["dependencies"]["opencv"]
        p = client.post("/api/revector/projects", json={"name": "API test", "settings": {"vector_mode": "precision"}}).json()
        pid = p["project_id"]
        assert client.post("/api/revector/upload", data={"project_id": pid}, files={"file": ("input.png", simple_bytes, "image/png")}).status_code == 200
        for stage in STAGES:
            response = client.post("/api/revector/" + stage, json={"project_id": pid})
            assert response.status_code == 202
            job = response.json()
            assert job["status"] == "completed", job
            assert client.get("/api/revector/jobs/" + job["job_id"]).json()["status"] == "completed"
        status = client.get(f"/api/revector/projects/{pid}/status").json()
        assert status["true_vector_ready"]
        result = client.post("/api/revector/export", json={"project_id": pid, "formats": ["svg", "png", "zip"]}).json()
        assert result["status"] == "completed"
        artifact = client.get(f"/api/revector/projects/{pid}/artifacts/exports/production-pack.zip")
        assert artifact.status_code == 200
        with zipfile.ZipFile(BytesIO(artifact.content)) as archive:
            assert "master.svg" in archive.namelist()
            assert "metadata/validation.json" in archive.namelist()
            assert len([f for f in archive.namelist() if f.startswith("parts/")]) == 2
        assert client.get(f"/api/revector/projects/{pid}/artifacts/project.json").status_code == 404
        assert client.put(f"/api/revector/projects/{pid}/settings", json={"known_width_mm": 600}).status_code == 200
        # Physical offsets can be updated independently after the stored project is calibrated.
        assert client.put(f"/api/revector/projects/{pid}/settings", json={"bleed_mm": 3}).status_code == 200
        invalid = client.put(f"/api/revector/projects/{pid}/settings", json={"known_width_mm": None})
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "INVALID_SETTINGS"


def test_per_part_invalidation_and_rerun(engine):
    p = complete(engine, "multiple_panels.png")
    first, second = p.parts
    second_hash = engine.file_hash(second.vector)
    second_cache = dict(second.cache)
    bbox = first.bbox
    polygon = [[bbox[0] + 5, bbox[1] + 5], [bbox[0] + bbox[2] - 6, bbox[1] + 5],
               [bbox[0] + bbox[2] - 6, bbox[1] + bbox[3] - 6], [bbox[0] + 5, bbox[1] + bbox[3] - 6]]
    engine.manual(p.project_id, "update", first.part_id, {"polygon": polygon, "type": "front_body"})
    changed = engine.load(p.project_id)
    assert not changed.true_vector_ready and not changed.exports and not changed.validation
    for stage in ["reconstruct", "vectorize", "optimize"]:
        engine.run(p.project_id, stage, {"part_id": first.part_id})
    for stage in ["compose", "validate"]:
        engine.run(p.project_id, stage)
    p = engine.load(p.project_id)
    second = next(part for part in p.parts if part.part_id == second.part_id)
    assert second.cache == second_cache
    assert engine.file_hash(second.vector) == second_hash
    assert p.true_vector_ready


def test_cached_analysis_preserves_ready(engine):
    p = complete(engine)
    result = engine.run(p.project_id, "analyze")
    assert result["cached"]
    assert engine.load(p.project_id).true_vector_ready


def test_cancellation_never_ready(engine, simple_bytes):
    p = engine.create()
    engine.upload(p.project_id, simple_bytes, "input.png")
    with pytest.raises(EngineError) as caught:
        engine.run(p.project_id, "analyze", cancelled=lambda: True)
    assert caught.value.code == "JOB_CANCELLED"
    assert not engine.load(p.project_id).true_vector_ready


def test_changed_master_blocks_export(engine):
    p = complete(engine)
    engine.storage.put(p.master_svg, engine.storage.get(p.master_svg).replace(b"#502080", b"#ff0000"))
    # Force a content change even when the palette quantizer chose another fill.
    engine.storage.put(p.master_svg, engine.storage.get(p.master_svg) + b"\n")
    with pytest.raises(EngineError) as caught:
        engine.run(p.project_id, "export")
    assert caught.value.code == "STALE_VALIDATION"
    assert not engine.load(p.project_id).true_vector_ready


def test_manual_lock_and_rename(engine):
    p = complete(engine)
    part = p.parts[0]
    before = engine.file_hash(part.vector)
    p = engine.manual(p.project_id, "update", part.part_id, {"name": "Confirmed front", "type": "front_body", "locked": True})
    assert p.parts[0].locked and not p.true_vector_ready
    assert engine.file_hash(p.parts[0].vector) == before
    with pytest.raises(EngineError) as caught:
        engine.manual(p.project_id, "remove", part.part_id, {})
    assert caught.value.code == "PART_LOCKED"
    p = engine.manual(p.project_id, "update", part.part_id, {"locked": False})
    assert not p.parts[0].locked


def test_ocr_records_real_results_and_outlines(engine):
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract not available")
    p = engine.create(settings=ProcessingSettings(vector_mode="precision", ocr=True))
    engine.upload(p.project_id, (FIXTURES / "text.png").read_bytes(), "text.png")
    for stage in STAGES:
        engine.run(p.project_id, stage)
    p = engine.load(p.project_id)
    assert p.true_vector_ready
    results = [result for part in p.parts for result in part.ocr_results]
    # Small connected glyph components may not OCR as words; the full input OCR
    # must nevertheless return measured confidence and an approximate font claim.
    from app.providers.ocr import TesseractOCR
    from PIL import Image
    full_results = TesseractOCR().detect(Image.open(FIXTURES / "text.png"))
    assert full_results
    assert all(0 <= result["model_confidence"] <= 1 and result["font_match_status"] == "approximate" for result in full_results + results)


@pytest.mark.skipif(not shutil.which("inkscape"), reason="Inkscape CLI unavailable")
def test_real_pdf_eps_conversion(engine):
    p = complete(engine)
    result = engine.run(p.project_id, "export", {"formats": ["svg", "pdf", "eps", "zip"]})
    assert not result["export_errors"]
    assert engine.storage.get(result["exports"]["pdf"]).startswith(b"%PDF")
    assert engine.storage.get(result["exports"]["eps"]).startswith(b"%!PS")


def test_traversal_denied(engine):
    with pytest.raises(EngineError):
        engine.storage.get("../../etc/passwd")


def test_preset_changes_invalidate_vectors_not_analysis(engine):
    p = complete(engine)
    analysis = p.analysis
    p = engine.update_settings(p.project_id, {"preset": "ULTRA"})
    assert p.analysis == analysis and p.parts[0].clean_reference
    assert not p.parts[0].vector and not p.true_vector_ready
