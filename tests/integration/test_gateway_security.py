"""Gateway credentials, identity isolation, and streaming upload regressions."""
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings

KEY = 'test-only-engine-key-' + 'x' * 48


def headers(user='anon_alice'):
    return {'Authorization': 'Bearer ' + KEY, 'X-Revector-User': user}


def test_api_fails_closed_without_credentials(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.post('/api/revector/projects', json={}).status_code == 503
        assert client.get('/health').status_code == 200
        assert client.get('/health/ready').status_code == 503
        assert client.get('/workspace.js').status_code == 404
        assert client.get('/docs').status_code == 503


def test_wrong_key_and_missing_identity_are_rejected(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, api_key=KEY))) as client:
        assert client.post('/api/revector/projects', json={}).status_code == 401
        assert client.post('/api/revector/projects', headers={'Authorization': 'Bearer ' + KEY}, json={}).status_code == 401
        assert client.get('/health/ready', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.get('/health/ready', headers=headers()).status_code == 200


def test_cross_identity_projects_jobs_mutations_and_downloads_are_denied(tmp_path, simple_bytes):
    with TestClient(create_app(Settings(data_dir=tmp_path, api_key=KEY, sync_jobs=True))) as client:
        p = client.post('/api/revector/projects', headers=headers(), json={}).json()
        pid = p['project_id']
        assert p['user_id'] == 'anon_alice'
        assert client.post('/api/revector/projects', headers=headers(), json={'user_id': 'anon_bob'}).status_code == 403
        upload = client.post('/api/revector/upload', headers=headers(), data={'project_id': pid}, files={'file': ('input.png', simple_bytes, 'image/png')})
        assert upload.status_code == 200
        job = client.post('/api/revector/analyze', headers=headers(), json={'project_id': pid}).json()
        assert job['status'] == 'completed'
        for method, path, payload in [
            ('GET', f'/projects/{pid}', None), ('GET', f'/projects/{pid}/parts', None),
            ('GET', f'/projects/{pid}/artifacts/working/normalized.png', None),
            ('GET', f'/jobs/{job["job_id"]}', None), ('POST', f'/jobs/{job["job_id"]}/cancel', {}),
            ('POST', '/analyze', {'project_id': pid}), ('PUT', f'/projects/{pid}/settings', {'preset': 'FAST'}),
            ('DELETE', f'/projects/{pid}', None),
        ]:
            response = client.request(method, '/api/revector' + path, headers=headers('anon_bob'), json=payload)
            assert response.status_code == 404, (path, response.text)
        assert client.get(f'/api/revector/projects/{pid}', headers=headers()).status_code == 200


def test_chunked_multipart_without_content_length_is_bounded_and_supported(tmp_path, simple_bytes):
    with TestClient(create_app(Settings(data_dir=tmp_path, api_key=KEY))) as client:
        pid = client.post('/api/revector/projects', headers=headers(), json={}).json()['project_id']
        body = (f'--boundary\r\nContent-Disposition: form-data; name="project_id"\r\n\r\n{pid}\r\n'
                '--boundary\r\nContent-Disposition: form-data; name="file"; filename="input.png"\r\nContent-Type: image/png\r\n\r\n').encode() + simple_bytes + b'\r\n--boundary--\r\n'
        response = client.post('/api/revector/upload', headers={**headers(), 'Content-Type': 'multipart/form-data; boundary=boundary'}, content=iter([body[:80], body[80:]]))
        assert response.status_code == 200, response.text


def test_chunked_oversize_is_rejected_before_unlimited_spooling(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, api_key=KEY, max_upload_bytes=1024))) as client:
        pid = client.post('/api/revector/projects', headers=headers(), json={}).json()['project_id']
        start = (f'--boundary\r\nContent-Disposition: form-data; name="project_id"\r\n\r\n{pid}\r\n'
                 '--boundary\r\nContent-Disposition: form-data; name="file"; filename="input.png"\r\n\r\n').encode()
        response = client.post('/api/revector/upload', headers={**headers(), 'Content-Type': 'multipart/form-data; boundary=boundary'}, content=iter([start, b'x' * (2 * 1024 * 1024), b'\r\n--boundary--\r\n']))
        assert response.status_code == 413, response.text
