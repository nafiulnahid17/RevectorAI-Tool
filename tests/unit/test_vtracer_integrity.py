"""Regressions from the real striped jersey benchmark."""
from types import SimpleNamespace
import sys
import pytest
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.vector.vtracer_engine import trace
from app.validators.svg_validator import validate_svg
from xml.etree import ElementTree as ET

def fake_backend(monkeypatch, payload):
    def convert(source, destination, **kwargs):
        from pathlib import Path
        Path(destination).write_text(payload)
    monkeypatch.setitem(sys.modules, "vtracer", SimpleNamespace(convert_image_to_svg_py=convert))

def test_empty_placeholder_removed_without_removing_real_artwork(tmp_path, monkeypatch):
    fake_backend(monkeypatch, '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path d="" fill="#112233"/><path d="M0 0 L10 0 L10 10 Z" fill="#ff0000"/></svg>')
    root, metadata = trace(tmp_path / "input.png", tmp_path / "trace.svg", ProcessingSettings())
    report = validate_svg(ET.tostring(root))
    assert report["true_vector"] and report["path_count"] == 1
    assert metadata["discarded_empty_paths"] == 1
    assert root.get("viewBox") == "0 0 10 10"

@pytest.mark.parametrize("path", ["", "M0 0 L", "MNaN 0 L2 3 Z"])
def test_invalid_or_empty_only_trace_fails_for_fallback(tmp_path, monkeypatch, path):
    fake_backend(monkeypatch, f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"><path d="{path}" fill="#112233"/></svg>')
    with pytest.raises(EngineError, match="invalid vector geometry"):
        trace(tmp_path / "input.png", tmp_path / "trace.svg", ProcessingSettings())
