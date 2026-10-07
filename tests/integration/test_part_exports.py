"""Browser-facing contract and physical individual export regression checks."""
from io import BytesIO
import hashlib
import zipfile
import shutil
from xml.etree import ElementTree as ET
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
from app.core.exceptions import EngineError
from tests.integration.test_pipeline import complete


def test_part_measurements_in_export_and_selective_zip(engine):
    p = complete(engine)
    first, second = p.parts
    analysis = p.analysis.copy()
    second_vector = engine.file_hash(second.vector)
    p = engine.manual(p.project_id, 'update', first.part_id,
                      {'physical_width_mm': 520, 'physical_height_mm': 720, 'bleed_mm': 3, 'safe_zone_mm': 5})
    assert p.analysis == analysis
    assert engine.file_hash(p.parts[1].vector) == second_vector
    assert not p.true_vector_ready
    engine.run(p.project_id, 'compose')
    engine.run(p.project_id, 'validate')
    result = engine.run(p.project_id, 'export', {'part_ids': [first.part_id], 'formats': ['svg', 'zip']})
    assert not result['export_errors']
    root = ET.fromstring(engine.storage.get(result['part_files'][first.part_id]['svg']))
    assert root.get('width') == '520.000000mm'
    assert root.get('height') == '720.000000mm'
    assert any(e.get('id', '').endswith('BLEED_PATH') for e in root.iter())
    with zipfile.ZipFile(BytesIO(engine.storage.get(result['exports']['zip']))) as archive:
        assert 'master.svg' not in archive.namelist()
        assert sum(name.startswith('parts/') for name in archive.namelist()) == 1
        assert not any(second.part_id in name for name in archive.namelist())
    assert p.project_id in result['part_files'][first.part_id]['svg']


def test_bad_dimensions_do_not_invalidate_ready_project(engine):
    p = complete(engine)
    with pytest.raises(EngineError) as caught:
        engine.manual(p.project_id, 'update', p.parts[0].part_id, {'physical_width_mm': 500})
    assert caught.value.code == 'INVALID_PART_SETTINGS'
    assert engine.load(p.project_id).true_vector_ready


def test_changed_part_blocks_individual_export(engine):
    p = complete(engine)
    key = engine.key(p, f'vectors/{p.parts[0].part_id}.svg')
    engine.storage.put(key, engine.storage.get(key) + b'\n')
    with pytest.raises(EngineError) as caught:
        engine.run(p.project_id, 'export', {'part_id': p.parts[0].part_id, 'formats': ['svg']})
    assert caught.value.code == 'STALE_VALIDATION'
    assert not engine.load(p.project_id).true_vector_ready


def test_changed_part_blocks_full_production_pack(engine):
    p = complete(engine)
    key = engine.key(p, f'vectors/{p.parts[0].part_id}.svg')
    engine.storage.put(key, engine.storage.get(key) + b'\n')
    with pytest.raises(EngineError) as caught:
        engine.run(p.project_id, 'export', {'formats': ['zip']})
    assert caught.value.code == 'STALE_VALIDATION'


def test_color_edit_invalidates_downloads_and_survives_revalidation(engine):
    p = complete(engine)
    part = p.parts[0]
    root = ET.fromstring(engine.storage.get(part.vector))
    shape = next(e for e in root.iter() if e.tag.endswith('path') and e.get('id'))
    p = engine.edit_fill(p.project_id, part.part_id, part.part_id + '_' + shape.get('id'), '#ff0033')
    assert not p.true_vector_ready and not p.exports
    assert b'#ff0033' in engine.storage.get(p.parts[0].vector)
    engine.run(p.project_id, 'compose')
    engine.run(p.project_id, 'validate')
    assert engine.load(p.project_id).true_vector_ready


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ('inkscape','pdfinfo','pdfimages','gs')), reason='Inkscape + Poppler + Ghostscript required')
def test_individual_vector_pdf_eps_and_pack(engine):
    p = complete(engine)
    result = engine.run(p.project_id, 'export', {'part_id': p.parts[0].part_id, 'formats': ['pdf', 'eps', 'zip']})
    assert not result['export_errors']
    files = result['part_files'][p.parts[0].part_id]
    assert engine.storage.get(files['pdf']).startswith(b'%PDF')
    assert engine.storage.get(files['eps']).startswith(b'%!PS')
    with zipfile.ZipFile(BytesIO(engine.storage.get(result['exports']['zip']))) as archive:
        assert any(name.endswith('.eps') for name in archive.namelist())
        assert any(name.endswith('.pdf') for name in archive.namelist())


def test_project_manifest_exposes_part_download_but_no_unlisted_file(tmp_path, simple_bytes):
    with TestClient(create_app(Settings(allow_unauthenticated=True, data_dir=tmp_path, sync_jobs=True))) as client:
        engine = client.app.state.engine
        p = complete(engine)
        pid, part_id = p.project_id, p.parts[0].part_id
        response = client.post('/api/revector/export', json={'project_id': pid, 'part_id': part_id, 'formats': ['svg']})
        assert response.json()['status'] == 'completed'
        key = response.json()['result']['part_files'][part_id]['svg']
        assert client.get(f'/api/revector/projects/{pid}/artifacts/' + key.split(pid + '/')[1]).status_code == 200
        assert client.get(f'/api/revector/projects/{pid}/artifacts/project.json').status_code == 404


def test_selected_files_zip_is_distinct_from_production_pack(engine):
    p = complete(engine)
    ids = [part.part_id for part in p.parts[:2]]

    selected = engine.run(
        p.project_id,
        'export',
        {'part_ids': ids, 'formats': ['svg', 'zip'], 'bundle': 'selected_files'},
    )
    with zipfile.ZipFile(BytesIO(engine.storage.get(selected['exports']['zip']))) as archive:
        names = archive.namelist()
        assert names
        assert all('/' not in name for name in names)
        assert all(name.endswith('.svg') for name in names)
        assert not any(name.startswith('metadata/') for name in names)
        assert not any(name.startswith('previews/') for name in names)

    pack = engine.run(
        p.project_id,
        'export',
        {'part_ids': ids, 'formats': ['svg', 'zip'], 'bundle': 'production_pack'},
    )
    assert selected['exports']['zip'] != pack['exports']['zip']
    with zipfile.ZipFile(BytesIO(engine.storage.get(pack['exports']['zip']))) as archive:
        names = archive.namelist()
        assert any(name.startswith('parts/') for name in names)
        assert any(name.startswith('metadata/') for name in names)
        assert any(name.startswith('previews/') for name in names)
        assert 'README.txt' in names


def test_selected_bundle_remains_in_manifest_for_download(tmp_path, simple_bytes):
    with TestClient(create_app(Settings(allow_unauthenticated=True, data_dir=tmp_path, sync_jobs=True))) as client:
        engine = client.app.state.engine
        p = complete(engine)
        ids = [p.parts[0].part_id]
        result = engine.run(
            p.project_id,
            "export",
            {"part_ids": ids, "formats": ["svg", "zip"], "bundle": "selected_files"},
        )
        key = result["exports"]["zip"]
        public = client.get(f"/api/revector/projects/{p.project_id}").json()
        assert key in public["exports"].values()
        relative = key.split(p.project_id + "/", 1)[1]
        response = client.get(
            f"/api/revector/projects/{p.project_id}/artifacts/{relative}"
        )
        assert response.status_code == 200
        assert response.content.startswith(b"PK")
