"""Rare branding hues must survive a dominant neutral background and its shading."""

import cv2
import numpy as np
from PIL import Image

from app.pipeline.colors import quantize


def test_small_crest_colors_survive_white_body_shading():
    width, height = 320, 320
    gray = np.tile(np.linspace(218, 255, width, dtype=np.uint8), (height, 1))
    rgb = np.repeat(gray[..., None], 3, axis=2)
    colors = [(8, 36, 88), (22, 133, 45), (247, 202, 18)]
    for index, color in enumerate(colors):
        rgb[120:136, 120 + index * 18 : 136 + index * 18] = color
    output, palette, _ = quantize(Image.fromarray(rgb), 12, 5)
    result = np.asarray(output)[..., :3]
    for index, color in enumerate(colors):
        target = cv2.cvtColor(
            np.asarray(color, np.float32).reshape(1, 1, 3) / 255, cv2.COLOR_RGB2LAB
        )[0, 0]
        patch = result[120:136, 120 + index * 18 : 136 + index * 18]
        lab = cv2.cvtColor(patch.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
        assert np.linalg.norm(lab - target, axis=2).mean() < 8
    assert len(palette) <= 12
    assert len(np.unique(result[120:136, 120:172].reshape(-1, 3), axis=0)) >= 3


def test_palette_refinement_is_deterministic_and_preserves_transparency():
    rng = np.random.default_rng(9)
    image = Image.fromarray(rng.integers(0, 256, (64, 64, 4), dtype=np.uint8))
    a, palette_a, labels_a = quantize(image, 8, 5)
    b, palette_b, labels_b = quantize(image, 8, 5)
    assert np.array_equal(np.asarray(a), np.asarray(b))
    assert palette_a == palette_b and np.array_equal(labels_a, labels_b)
    assert np.array_equal(np.asarray(a)[..., 3], np.asarray(image)[..., 3])


def test_palette_upgrade_invalidates_existing_raster_master_cache(
    tmp_path, simple_bytes, monkeypatch
):
    from app.core.config import Settings
    from app.core.engine import Engine
    from app.pipeline import colors

    e = Engine(Settings(data_dir=tmp_path))
    p = e.create()
    e.upload(p.project_id, simple_bytes, "source.png")
    for stage in ["analyze", "correct-geometry", "segment", "reconstruct"]:
        e.run(p.project_id, stage)
    assert e.run(p.project_id, "reconstruct")["cached"]
    monkeypatch.setattr(colors, "PALETTE_VERSION", "next-palette-version")
    assert not e.run(p.project_id, "reconstruct")["cached"]
