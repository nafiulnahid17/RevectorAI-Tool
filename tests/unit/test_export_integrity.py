"""Reject conversion-time rasterization rather than trusting a PDF file signature."""
import shutil
from pathlib import Path
import pytest
from PIL import Image
from app.pipeline.export import verify_conversion, _physical_inches
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
