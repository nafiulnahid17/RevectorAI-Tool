from PIL import Image, ImageDraw

from app.pipeline.hybrid_detection import MATCHING_VERSION, refine


def _fixture():
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
    return image, boxes, types


def test_stable_699ac010_overlap_matcher_assigns_clear_components():
    image, boxes, types = _fixture()
    candidates = []
    for part_type, (left, top, right, bottom) in zip(types, boxes):
        candidates.append(
            {
                "part_type": part_type,
                "candidate_bbox": [
                    left / image.width,
                    top / image.height,
                    (right - left) / image.width,
                    (bottom - top) / image.height,
                ],
                "confidence": 0.9,
                "uncertain": False,
                "notes": "",
            }
        )

    assignments, _ = refine(image, candidates, 0.0005)
    matched = [item["candidate"] for item in assignments if item["candidate"]]

    assert MATCHING_VERSION == "candidate-boundary-match/1.0"
    assert len(matched) == 8
    assert {item["part_type"] for item in matched} == set(types)


def test_stable_699ac010_overlap_matcher_rejects_too_tight_ai_box():
    image = Image.new("RGB", (400, 300), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 250, 250), fill="white")

    # This box covers well under 65% of the component mask, so the restored
    # matcher must leave the CV component unclassified for manual review.
    candidates = [
        {
            "part_type": "FRONT_BODY",
            "candidate_bbox": [0.25, 0.30, 0.25, 0.30],
            "confidence": 0.9,
            "uncertain": False,
            "notes": "",
        }
    ]

    assignments, _ = refine(image, candidates, 0.0005)
    assert assignments
    assert assignments[0]["candidate"] is None
