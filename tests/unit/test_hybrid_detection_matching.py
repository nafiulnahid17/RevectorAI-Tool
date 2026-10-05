import numpy as np
from PIL import Image, ImageDraw

from app.pipeline.hybrid_detection import refine


def test_refine_matches_tight_ai_boxes_to_real_components():
    width, height = 800, 600
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)

    boxes = [
        (40, 120, 150, 420),
        (180, 90, 330, 430),
        (365, 90, 515, 430),
        (550, 120, 660, 420),
        (220, 455, 305, 520),
        (390, 455, 475, 520),
        (260, 35, 460, 65),
        (260, 540, 460, 570),
    ]
    types = [
        "LEFT_SLEEVE",
        "FRONT_BODY",
        "BACK_BODY",
        "RIGHT_SLEEVE",
        "FRONT_COLLAR",
        "BACK_COLLAR",
        "TOP_TRIM",
        "BOTTOM_TRIM",
    ]

    for box in boxes:
        draw.rectangle(box, fill="white")

    candidates = []
    for part_type, (left, top, right, bottom) in zip(types, boxes):
        w = right - left
        h = bottom - top
        # Deliberately tighter than the real component. The old >=65% mask-overlap
        # gate rejected these despite the centers and boxes clearly corresponding.
        inset_x = w * 0.18
        inset_y = h * 0.18
        x = (left + inset_x) / width
        y = (top + inset_y) / height
        cw = (w - 2 * inset_x) / width
        ch = (h - 2 * inset_y) / height
        candidates.append(
            {
                "part_type": part_type,
                "candidate_bbox": [x, y, cw, ch],
                "confidence": 0.9,
                "uncertain": False,
                "notes": "",
            }
        )

    assignments, metadata = refine(image, candidates, 0.0005)

    matched = [item["candidate"] for item in assignments if item["candidate"]]
    assert len(matched) == 8
    assert {item["part_type"] for item in matched} == set(types)
    assert metadata["matching"]["matched_count"] == 8
    assert metadata["matching"]["unmatched_candidates"] == 0
