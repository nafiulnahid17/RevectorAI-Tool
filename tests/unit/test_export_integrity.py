"""Reject conversion-time rasterization rather than trusting a PDF file signature."""
import shutil
from pathlib import Path
import pytest
from PIL import Image
from app.pipeline.export import verify_conversion, _physical_inches, output_profile, _validate_eps_page_size
from app.core.exceptions import EngineError

@pytest.mark.skipif(not shutil.which('pdfimages'),reason='Poppler unavailable')
def test_raster_pdf_is_rejected_in_true_vector_mode(tmp_path):
    target=tmp_path/'fake-vector.pdf';Image.new('RGB',(32,32),'purple').save(target,'PDF')
    with pytest.raises(EngineError) as error:verify_conversion(target,'pdf',30,'true_vector')
    assert error.value.code=='RASTER_FOUND_IN_TRUE_VECTOR'


def test_missing_export_parser_preserves_safe_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda tool:None)
    with pytest.raises(EngineError) as error:verify_conversion(tmp_path/'output.pdf','pdf',1,'true_vector')
    assert error.value.code=='EXPORT_VALIDATION_UNAVAILABLE'


def test_physical_units_map_to_expected_300_dpi_dimensions():
    assert round(_physical_inches("558.8mm") * 300) == 6600
    assert round(_physical_inches("787.4mm") * 300) == 9300
    assert _physical_inches("1536px") is None


def test_eps_profile_reports_client_handoff_standard():
    payload = (
        b"%!PS-Adobe-3.0 EPSF-3.0\n"
        b"%%LanguageLevel: 2\n"
        b"%%BoundingBox: 0 0 1584 2232\n"
        b"%%HiResBoundingBox: 0 0 1584.0 2232.0\n"
    )
    profile = output_profile(payload, "eps")
    assert profile["epsf"] == "3.0"
    assert profile["postscript_level"] == 2
    assert profile["color_mode"] == "CMYK"
    assert profile["bounding_box_pt"] == [0, 0, 1584, 2232]
    assert profile["resolution_independent"] is True
    assert profile["created_by"] == "ReVector"


def test_body_eps_bounding_box_matches_22_by_31_inches():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="558.8mm" height="787.4mm" '
        b'viewBox="0 0 558.8 787.4"><path d="M0 0H10V10H0Z"/></svg>'
    )
    eps = (
        b"%!PS-Adobe-3.0 EPSF-3.0\n"
        b"%%LanguageLevel: 2\n"
        b"%%BoundingBox: 0 0 1584 2232\n"
        b"%%HiResBoundingBox: 0 0 1584.0 2232.0\n"
    )
    profile = _validate_eps_page_size(svg, eps)
    assert profile["physical_size_match"] is True
    assert profile["expected_points"] == [1584.0, 2232.0]
