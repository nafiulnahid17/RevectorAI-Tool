"""New routes preserve ownership and explicit job terminal/error contracts."""

from threading import Event

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.engine import Engine
from app.jobs.queue import LocalJobQueue
from app.main import create_app
from tests.integration.test_gateway_security import KEY, headers


def test_all_upgrade_routes_are_owner_checked(tmp_path):
    with TestClient(
        create_app(Settings(data_dir=tmp_path, api_key=KEY, sync_jobs=True))
    ) as c:
        pid = c.post("/api/revector/projects", json={}, headers=headers()).json()[
            "project_id"
        ]
        for endpoint, payload in [
            ("/prepare", {}),
            ("/production", {}),
            ("/recover-part", {"part_id": "part_test"}),
            ("/ai-missing", {"slot": "LEFT_SLEEVE"}),
            ("/slots/update", {"slot": "LEFT_SLEEVE", "status": "blank"}),
            ("/review/confirm", {"decisions": {}}),
            ("/assistant/explain", {"question": "Why?"}),
            (
                "/assistant/feedback",
                {"error_id": "test", "action": "retry_stage", "outcome": "SUCCESS"},
            ),
        ]:
            r = c.post(
                "/api/revector" + endpoint,
                headers=headers("anon_bob"),
                json={"project_id": pid, **payload},
            )
            assert r.status_code == 404, (endpoint, r.text)
        for endpoint in ["/events", "/errors"]:
            assert (
                c.get(
                    "/api/revector/projects/" + pid + endpoint,
                    headers=headers("anon_bob"),
                ).status_code
                == 404
            )
        assert c.post("/api/revector/assistant/explain", json={}).status_code == 401


def test_cancelled_job_has_explicit_terminal_state_and_error_event(
    tmp_path, simple_bytes
):
    e = Engine(Settings(data_dir=tmp_path, sync_jobs=True))
    p = e.create()
    e.upload(p.project_id, simple_bytes, "source.png")
    q = LocalJobQueue(e)
    job = {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "project_id": p.project_id,
        "stage": "analyze",
        "status": "queued",
        "created_at": "test",
    }
    event = Event()
    event.set()
    q.execute(job, {}, event)
    stored = q.get(job["job_id"])
    assert (
        stored["job_state"] == "CANCELLED"
        and stored["completed_at"]
        and stored["error"]["error_code"] == "JOB_CANCELLED"
    )
    assert e.load(p.project_id).events[-1]["event"] == "ERROR_OCCURRED"
    q.shutdown()


def test_job_timeout_is_normalized_not_stuck(tmp_path, simple_bytes, monkeypatch):
    e = Engine(Settings(data_dir=tmp_path, sync_jobs=True, job_timeout_seconds=1))
    p = e.create()
    e.upload(p.project_id, simple_bytes, "source.png")
    q = LocalJobQueue(e)
    from unittest.mock import Mock

    monkeypatch.setattr("time.monotonic", Mock(side_effect=[0, 10]))
    job = q.submit(p.project_id, "analyze")
    assert (
        job["job_state"] == "FAILED"
        and job["error"]["error_code"] == "JOB_TIMEOUT"
        and job["completed_at"]
    )
    assert e.load(p.project_id).error["error_id"] == job["error"]["error_id"]
    q.shutdown()


def test_restart_marks_interrupted_job_failed(tmp_path):
    e = Engine(Settings(data_dir=tmp_path))
    p = e.create()
    key = "jobs/11111111-1111-1111-1111-111111111111.json"
    e.storage.json(
        key,
        {
            "job_id": "11111111-1111-1111-1111-111111111111",
            "project_id": p.project_id,
            "stage": "production",
            "status": "processing",
            "created_at": "test",
        },
    )
    q = LocalJobQueue(e)
    job = q.get("11111111-1111-1111-1111-111111111111")
    assert (
        job["job_state"] == "FAILED"
        and job["error"]["error_id"]
        and job["completed_at"]
    )
    q.shutdown()


def test_error_assistant_remains_available_during_project_processing(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, api_key=KEY))) as client:
        pid = client.post("/api/revector/projects", json={}, headers=headers()).json()[
            "project_id"
        ]
        engine = client.app.state.engine
        before = engine.load(pid).model_dump()
        with engine.storage.lock(pid):
            response = client.post(
                "/api/revector/assistant/explain",
                headers=headers(),
                json={
                    "project_id": pid,
                    "error": {
                        "error_code": "VECTOR_TRACE_FAILED",
                        "phase": "vectorize",
                    },
                    "question": "Can I get help while other parts are processing?",
                },
            )
        assert response.status_code == 200
        assert response.json()["advice"]["source"] == "deterministic_catalog"
        assert response.json()["memory_persisted"] is False
        assert engine.load(pid).model_dump() == before
