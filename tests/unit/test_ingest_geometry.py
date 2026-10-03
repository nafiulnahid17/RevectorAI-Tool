from io import BytesIO
import numpy as np
import pytest
from PIL import Image
from app.pipeline.ingest import ingest
from app.pipeline.geometry import correct
from app.core.exceptions import EngineError


def test_exif_rotation_preserves_resolution():
    image = Image.new("RGB", (20, 30), "red")
    exif = image.getexif(); exif[274] = 6
    data = BytesIO(); image.save(data, "JPEG", exif=exif)
    result, meta = ingest(data.getvalue(), "a.jpg", "image/jpeg", 100000, 10000)
    assert result.size == (30, 20)
    assert meta["original_dimensions"] == [20, 30]
    assert meta["exif_orientation_applied"]


@pytest.mark.parametrize("filename,mime", [("x.webp", "image/png"), ("x.png", "image/jpeg")])
def test_type_and_mime_match(simple_bytes, filename, mime):
    with pytest.raises(EngineError):
        ingest(simple_bytes, filename, mime, 1000000, 1000000)


def test_limits_and_corruption(simple_bytes):
    for data, max_bytes, pixels in [(simple_bytes, 100, 1000000), (simple_bytes, 100000, 100), (b"broken", 1000, 10000)]:
        with pytest.raises(EngineError):
            ingest(data, "a.png", "image/png", max_bytes, pixels)


def test_homography_and_recorded_inverse():
    image = Image.new("RGBA", (100, 80), "red")
    output, meta = correct(image, [[10, 10], [89, 8], [92, 69], [8, 70]])
    assert output.width > 70 and output.height > 50
    assert meta["crop_applied"]
    assert np.allclose(np.array(meta["matrix"]) @ np.array(meta["inverse_matrix"]), np.eye(3))


def test_invalid_corners():
    with pytest.raises(EngineError):
        correct(Image.new("RGB", (100, 100)), [[0, 0], [1, 1], [2, 2], [3, 3]])
