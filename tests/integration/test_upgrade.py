"""AI reference orchestration exercises real CV, tracing, render and export modules."""

from io import BytesIO
import zipfile
from PIL import Image, ImageDraw
import pytest
from fastapi.testclient import TestClient
from app.ai.contracts import SLOTS
from app.ai.router import AIRouter
from app.core.engine import Engine
from app.core.config import Settings
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.main import create_app


def sheet():
    image = Image.new("RGB", (1024, 768), "black")
    d = ImageDraw.Draw(image)
    boxes = []
    for i in range(8):
        x = 20 + (i % 4) * 250
        y = 30 + (i // 4) * 350
        d.rounded_rectangle((x, y, x + 170, y + 260), radius=18, fill="#682faa")
        d.rectangle((x + 35, y + 90, x + 120, y + 115), fill="white")
        boxes.append([x / 1024, y / 768, 170 / 1024, 260 / 768])
    return image, boxes


class MockArtworkProvider:
    name = "test-ai"

    def analyze_artwork(self, image):
        return {
            "artwork_type": "jersey",
            "missing_parts": ["BACK_BODY"],
            "notes": ["Back inferred"],
        }

    def enhance_artwork(self, image, size):
        return sheet()[0]

    def create_pattern_mockup(self, image, prompt, size):
        assert "Do NOT force an eight-part template" in prompt
        return sheet()[0]

    def identify_parts(self, image):
        return [
            {"part_type": name, "candidate_bbox": bbox, "confidence": None}
            for name, bbox in zip(SLOTS, sheet()[1])
        ]

    def reconstruct_missing_part(self, image, prompt, size):
        img = Image.new("RGB", (1024, 1024), "black")
        ImageDraw.Draw(img).rectangle((100, 150, 900, 900), fill="purple")
        return img


def prepared(tmp_path):
    e = Engine(
        Settings(data_dir=tmp_path), ai_router=AIRouter(primary=MockArtworkProvider())
    )
    p = e.create(
        settings=ProcessingSettings(
            vector_mode="precision",
            gradients=False,
            mockup_width=1024,
            mockup_height=768,
        )
    )
    stream = BytesIO()
    sheet()[0].save(stream, format="PNG")
    e.upload(p.project_id, stream.getvalue(), "source.png")
    e.run(p.project_id, "prepare")
    return e, e.load(p.project_id)


def confirm(e, p, selected=None):
    chosen = list(selected or p.parts)
    decisions = {}
    ids = []
    for a in chosen:
        changes = {"confirmed": True}
        if a.type not in {"front_body", "back_body"}:
            changes.update(physical_width_mm=250.0, physical_height_mm=350.0)
        e.manual(p.project_id, "update", a.part_id, changes)
        ids.append(a.part_id)
        if a.type.upper() in SLOTS:
            decisions[a.type.upper()] = {"status": "confirmed", "part_id": a.part_id}
    e.workflow.review(p.project_id, ids, decisions)


def test_provider_supported_four_three_mockup_is_accepted_for_legacy_three_two_settings(tmp_path):
    e = Engine(
        Settings(data_dir=tmp_path), ai_router=AIRouter(primary=MockArtworkProvider())
    )
    p = e.create(
        settings=ProcessingSettings(
            vector_mode="precision",
            gradients=False,
            mockup_width=1536,
            mockup_height=1024,
        )
    )
    stream = BytesIO()
    sheet()[0].save(stream, format="PNG")
    e.upload(p.project_id, stream.getvalue(), "source.png")

    e.run(p.project_id, "prepare")
    current = e.load(p.project_id)

    assert len(current.parts) == 8
    assert current.ai_metadata["mockup"]["requested_dimensions"] == [1536, 1024]
    assert current.ai_metadata["mockup"]["requested_aspect_ratio"] == "4:3"
    assert current.ai_metadata["mockup"]["actual_dimensions"] == [1024, 768]


def test_new_project_default_mockup_canvas_is_four_three():
    settings = ProcessingSettings()
    assert (settings.mockup_width, settings.mockup_height) == (1536, 1152)


def test_review_locks_client_body_dimensions_only(tmp_path):
    e, p = prepared(tmp_path)
    confirm(e, p)
    current = e.load(p.project_id)
    body = [part for part in current.parts if part.type in {"front_body", "back_body"}]
    others = [part for part in current.parts if part.type not in {"front_body", "back_body"}]
    assert body and all(part.physical_width_mm == 558.8 for part in body)
    assert all(part.physical_height_mm == 787.4 for part in body)
    assert others and all(part.physical_width_mm == 250.0 for part in others)
    assert all(part.physical_height_mm == 350.0 for part in others)


def test_two_selected_parts_do_not_require_eight_part_confirmation(tmp_path):
    e, p = prepared(tmp_path)
    selected = [
        next(part for part in p.parts if part.type == "front_body"),
        next(part for part in p.parts if part.type == "back_body"),
    ]
    confirm(e, p, selected)
    untouched = [part for part in e.load(p.project_id).parts if part.part_id not in {x.part_id for x in selected}]
    assert untouched and all(not part.confirmed for part in untouched)

    e.run(p.project_id, "production")
    current = e.load(p.project_id)
    assert current.true_vector_ready
    assert current.validation["selected_part_ids"] == [part.part_id for part in selected]
    assert len(current.validation["parts"]) == 2
    assert all(entry["resolution_independent"] for entry in current.validation["parts"])

    result = e.run(
        p.project_id,
        "export",
        {
            "part_ids": [part.part_id for part in selected],
            "formats": ["svg", "zip"],
            "bundle": "production_pack",
        },
    )
    assert len(result["part_files"]) == 2


def test_full_eight_slot_pipeline_exports_only_parts(tmp_path):
    e, p = prepared(tmp_path)
    assert len(p.parts) == 8 and set(p.slots) == set(SLOTS)
    assert p.ai_metadata["mockup"]["intermediate_raster"]
    assert not p.true_vector_ready
    assert all(a.source == "engine_refined" and a.polygon for a in p.parts)
    assert p.usage["ai_calls"] == 4
    confirm(e, p)
    e.run(p.project_id, "production")
    p = e.load(p.project_id)
    assert p.true_vector_ready and p.validation["embedded_rasters"] == 0
    result = e.run(
        p.project_id,
        "export",
        {"formats": ["svg", "zip"], "bundle": "production_pack"},
    )
    assert len(result["part_files"]) == 8 and set(result["exports"]) == {"zip"}
    archive = zipfile.ZipFile(BytesIO(e.storage.get(result["exports"]["zip"])))
    assert sum(name.startswith("parts/") for name in archive.namelist()) == 8
    assert not any(
        "master.svg" in name or "vector_view.svg" in name for name in archive.namelist()
    )
    assert e.load(p.project_id).events[-1]["event"] == "EXPORT_READY"


def test_single_failure_preserves_other_parts_and_retry(tmp_path, monkeypatch):
    e, p = prepared(tmp_path)
    confirm(e, p)
    failed = p.parts[2].part_id
    original = e.stage_vectorize

    def fail_one(project, params, cancelled):
        if params["part_id"] == failed:
            raise EngineError("INVALID_PATH_GEOMETRY", "Malformed trace")
        return original(project, params, cancelled)

    monkeypatch.setattr(e, "stage_vectorize", fail_one)
    with pytest.raises(EngineError):
        e.run(p.project_id, "production")
    current = e.load(p.project_id)
    assert sum(bool(a.vector) for a in current.parts) == 7
    hashes = {a.part_id: e.file_hash(a.vector) for a in current.parts if a.vector}
    assert (
        next(a for a in current.parts if a.part_id == failed).error["error_code"]
        == "INVALID_PATH_GEOMETRY"
    )
    monkeypatch.setattr(e, "stage_vectorize", original)
    e.run(p.project_id, "recover-part", {"part_id": failed, "fallback_trace": True})
    current = e.load(p.project_id)
    assert current.true_vector_ready
    assert all(
        e.file_hash(a.vector) == hashes[a.part_id]
        for a in current.parts
        if a.part_id in hashes
    )


def test_missing_blank_and_generated_use_same_cv_pipeline(tmp_path):
    e, p = prepared(tmp_path)
    part = p.parts[0]
    e.manual(p.project_id, "remove", part.part_id, {})
    p = e.load(p.project_id)
    assert p.slots[part.type.upper()].status == "missing"
    e.run(p.project_id, "ai-missing", {"slot": part.type.upper()})
    p = e.load(p.project_id)
    restored = next(a for a in p.parts if a.type == part.type)
    assert restored.source == "ai_reconstructed" and not restored.confirmed
    confirm(e, p)
    e.run(p.project_id, "production")
    assert e.load(p.project_id).true_vector_ready


def test_no_credentials_keeps_deterministic_path_and_no_fabricated_slots(tmp_path):
    e = Engine(Settings(data_dir=tmp_path), ai_router=AIRouter(AISettings()))
    p = e.create()
    stream = BytesIO()
    sheet()[0].save(stream, format="PNG")
    e.upload(p.project_id, stream.getvalue(), "input.png")
    e.run(p.project_id, "prepare")
    p = e.load(p.project_id)
    assert p.usage["ai_calls"] == 0 and not p.ai_assets
    assert all(slot.status == "missing" for slot in p.slots.values())
    assert all(a.type == "unknown" for a in p.parts)
    with pytest.raises(EngineError):
        e.run(p.project_id, "production")


def test_api_errors_normalized_and_master_artifact_denied(tmp_path):
    with TestClient(
        create_app(
            Settings(allow_unauthenticated=True, data_dir=tmp_path, sync_jobs=True)
        )
    ) as c:
        pid = c.post("/api/revector/projects", json={}).json()["project_id"]
        response = c.post("/api/revector/production", json={"project_id": pid}).json()
        assert response["job_state"] == "FAILED"
        assert response["error"]["error_id"] and response["completed_at"]
        assert (
            c.get(
                f"/api/revector/projects/{pid}/artifacts/vectors/master.svg"
            ).status_code
            == 404
        )
        advice = c.post(
            "/api/revector/assistant/explain",
            json={
                "project_id": pid,
                "error_id": response["error"]["error_id"],
                "question": "Why?",
            },
        ).json()["advice"]
        assert advice["source"] == "deterministic_catalog"
        response = c.post(
            "/api/revector/assistant/feedback",
            json={
                "project_id": pid,
                "error_id": "unknown",
                "action": "delete_project",
                "outcome": "SUCCESS",
            },
        )
        assert response.status_code == 422
        response = c.post(
            "/api/revector/analyze",
            json={"project_id": pid, "secret": "should-not-echo"},
        )
        assert response.status_code == 422 and "should-not-echo" not in response.text


from app.core.config import AISettings


@pytest.mark.parametrize(
    "slot", ["LEFT_SLEEVE", "FRONT_COLLAR", "BACK_COLLAR", "TOP_TRIM", "BOTTOM_TRIM"]
)
def test_blank_missing_slot_never_fabricates_geometry(tmp_path, slot):
    e, p = prepared(tmp_path)
    removed = next(a for a in p.parts if a.type == slot.lower())
    e.manual(p.project_id, "remove", removed.part_id, {})
    p = e.load(p.project_id)
    decisions = {slot: {"status": "blank"}}
    ids = []
    for a in p.parts:
        changes = {"confirmed": True}
        if a.type not in {"front_body", "back_body"}:
            changes.update(physical_width_mm=250.0, physical_height_mm=350.0)
        e.manual(p.project_id, "update", a.part_id, changes)
        ids.append(a.part_id)
        decisions[a.type.upper()] = {"status": "confirmed", "part_id": a.part_id}
    e.workflow.review(p.project_id, ids, decisions)
    p = e.load(p.project_id)
    assert len(p.parts) == 7 and p.slots[slot].status == "blank"
    assert p.slots[slot].part_id is None


def test_browser_error_followup_retains_error_session_without_secrets(tmp_path):
    with TestClient(
        create_app(
            Settings(allow_unauthenticated=True, data_dir=tmp_path, sync_jobs=True)
        )
    ) as c:
        pid = c.post("/api/revector/projects", json={}).json()["project_id"]
        first = c.post(
            "/api/revector/assistant/explain",
            json={
                "project_id": pid,
                "error": {"error_code": "NETWORK_ERROR", "message": "Network failed"},
                "question": "Why?",
            },
        ).json()
        second = c.post(
            "/api/revector/assistant/explain",
            json={
                "project_id": pid,
                "error_id": first["error_id"],
                "question": "Can I retry?",
            },
        ).json()
        assert first["error_id"] == second["error_id"]
        p = c.app.state.engine.load(pid)
        assert len(p.assistant_sessions[first["error_id"]]) == 4
        assert (
            c.get("/api/revector/projects/" + pid).json().get("assistant_sessions")
            is None
        )


def test_old_assembled_downloads_are_not_inherited(engine):
    p = engine.create()
    p.exports = {
        "svg": f"projects/{p.project_id}/exports/master.svg",
        "selected_zip_old": f"projects/{p.project_id}/exports/old.zip",
    }
    engine.storage.save(p)
    assert not engine.load(p.project_id).exports


def test_preparation_cache_verifies_reference_content(tmp_path):
    e, p = prepared(tmp_path)
    assert e.run(p.project_id, "prepare")["cached"]
    # Reference bytes changed in private storage: stale cache must not be approved.
    e.storage.put(p.corrected_image, b"changed")
    result = e.run(p.project_id, "prepare")
    assert not result.get("cached")


def test_auto_prepare_upload_keeps_legacy_upload_contract(tmp_path, simple_bytes):
    with TestClient(
        create_app(
            Settings(allow_unauthenticated=True, data_dir=tmp_path, sync_jobs=True)
        )
    ) as c:
        pid = c.post("/api/revector/projects", json={}).json()["project_id"]
        response = c.post(
            "/api/revector/upload",
            data={"project_id": pid, "auto_prepare": "true"},
            files={"file": ("source.png", simple_bytes, "image/png")},
        )
        data = response.json()
        assert data["preparation_job"]["job_state"] == "SUCCEEDED"
        assert data["state"] == "PART_REVIEW_READY"
        assert len(data["slots"]) == 8 and not data["true_vector_ready"]
