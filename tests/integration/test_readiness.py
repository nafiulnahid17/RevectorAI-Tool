"""Readiness must reflect real render/storage failures, not just live HTTP."""
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
import app.core.readiness as checks


def test_ready_server_runs_vector_and_storage_probes(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get('/health/ready')
        assert response.status_code == 200
        report = response.json()
        assert report['status'] == 'ready'
        assert all(segment['status'] == 'connected' for segment in report['segments'].values())
        assert report['segments']['engine']['checks']['vector_render']
        assert report['segments']['tool']['checks']['storage']
        assert response.headers['cache-control'] == 'no-store'
        assert not list(tmp_path.glob('tmp*'))


def test_live_server_is_not_ready_when_vector_renderer_fails(tmp_path, monkeypatch):
    def failed_render(_data):
        raise RuntimeError('Renderer failed')
    monkeypatch.setattr(checks, 'render_svg', failed_render)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get('/health').status_code == 200
        response = client.get('/health/ready')
        assert response.status_code == 503
        assert response.json()['segments']['server']['status'] == 'connected'
        assert response.json()['segments']['engine']['status'] == 'failed'
        assert response.json()['segments']['tool']['status'] == 'failed'


def test_storage_failure_marks_tool_red_and_keeps_engine_connected(tmp_path, monkeypatch):
    def failed_storage(*_args, **_kwargs):
        raise PermissionError('Storage unavailable')
    monkeypatch.setattr(checks, 'NamedTemporaryFile', failed_storage)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get('/health/ready')
        assert response.status_code == 503
        segments = response.json()['segments']
        assert segments['engine']['status'] == 'connected'
        assert segments['tool']['status'] == 'failed'
