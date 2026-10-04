import numpy as np
from PIL import Image
from app.pipeline.colors import quantize
from app.pipeline.segmentation import detect_masks, manual_mask
from app.vector.contour import trace
from app.vector.simplify import optimize
from app.vector.gradients import fit_linear
from app.models.project import ProcessingSettings
from app.validators.svg_validator import validate_svg
from xml.etree import ElementTree as ET
from tests.conftest import FIXTURES


def test_palette_ignores_transparency():
    image = Image.new("RGBA", (30, 30))
    image.paste((90, 20, 140, 255), (10, 10, 20, 20))
    _, palette, labels = quantize(image, 4, 5)
    assert len(palette) == 1
    assert palette[0]["rgb"] == [90, 20, 140]
    assert labels[0, 0] == -1


def test_multiple_panels_and_manual_boundary():
    image = Image.open(FIXTURES / "multiple_panels.png")
    masks, meta = detect_masks(image, .01)
    assert len(masks) == 2 and meta["confidence"] is None
    assert manual_mask(image.size, [[1, 1], [40, 1], [40, 30], [1, 30]]).sum() > 0


def test_cleanup_reduces_real_anchors():
    image = Image.open(FIXTURES / "simple_2_color.png")
    root, _ = trace(image, ProcessingSettings())
    stats = optimize(root, "BALANCED")
    assert stats["final_anchor_count"] < stats["initial_anchor_count"]
    assert validate_svg(ET.tostring(root))["true_vector"]


def test_gradient_is_real_gradient_with_measured_residual():
    result = fit_linear(Image.open(FIXTURES / "gradient.png"))
    assert result is not None
    root, meta = result
    report = validate_svg(ET.tostring(root))
    assert report["true_vector"] and report["gradient_count"] == 1
    assert meta["rmse_rgb"] < 1


def test_irregular_texture_not_claimed_as_gradient():
    assert fit_linear(Image.open(FIXTURES / "splatter.png")) is None


def test_native_timeout_uses_contours_without_accepting_invalid_geometry(monkeypatch):
    from app.pipeline.vectorization import vectorize
    from app.vector import vtracer_engine
    from app.core.exceptions import EngineError
    from PIL import Image,ImageDraw
    from app.models.project import ProcessingSettings
    image=Image.new('RGBA',(80,80));ImageDraw.Draw(image).rectangle((10,10,70,70),fill='purple')
    def timeout(*args,**kwargs):raise EngineError('VECTOR_TRACE_FAILED','native timed out')
    monkeypatch.setattr(vtracer_engine,'trace',timeout)
    root,metadata=vectorize(image,image,ProcessingSettings(gradients=False),2)
    assert metadata['backend']=='opencv_color_contours' and metadata['fallback_attempted']
    assert metadata['attempts'][0]['status']=='FAILED'
