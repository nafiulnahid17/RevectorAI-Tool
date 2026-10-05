"""AI classifies connected components; only deterministic pixel masks define geometry."""

import cv2
import numpy as np
from PIL import Image

from app.pipeline.segmentation import detect_masks


def _pixel_bbox(candidate: dict, width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = candidate["candidate_bbox"]
    left = max(0, min(width - 1, int(round(x * width))))
    top = max(0, min(height - 1, int(round(y * height))))
    right = max(left + 1, min(width, int(round((x + w) * width))))
    bottom = max(top + 1, min(height, int(round((y + h) * height))))
    return left, top, right, bottom


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return intersection / max(area_a + area_b - intersection, 1)


def _component_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask > 0)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _pair_score(
    mask: np.ndarray,
    component_bbox: tuple[int, int, int, int],
    candidate: dict,
    width: int,
    height: int,
) -> tuple[float, dict]:
    candidate_bbox = _pixel_bbox(candidate, width, height)
    left, top, right, bottom = candidate_bbox
    area = max(1, int(np.count_nonzero(mask)))
    mask_overlap = float(np.count_nonzero(mask[top:bottom, left:right])) / area
    bbox_iou = _iou(component_bbox, candidate_bbox)

    cx = (component_bbox[0] + component_bbox[2]) / 2
    cy = (component_bbox[1] + component_bbox[3]) / 2
    qx = (candidate_bbox[0] + candidate_bbox[2]) / 2
    qy = (candidate_bbox[1] + candidate_bbox[3]) / 2
    distance = float(np.hypot(cx - qx, cy - qy))
    diagonal = max(float(np.hypot(width, height)), 1.0)
    center_score = max(0.0, 1.0 - distance / (0.30 * diagonal))

    score = 0.50 * bbox_iou + 0.35 * mask_overlap + 0.15 * center_score
    viable = bbox_iou >= 0.08 or mask_overlap >= 0.20 or center_score >= 0.82
    return score if viable else -1.0, {
        "bbox_iou": round(bbox_iou, 4),
        "mask_overlap": round(mask_overlap, 4),
        "center_score": round(center_score, 4),
    }


def refine(image: Image.Image, candidates: list[dict], min_ratio: float):
    masks, metadata = detect_masks(image, min_ratio)
    if metadata["method"] == "whole_artboard_fallback":
        return [], {
            **metadata,
            "warnings": [
                *metadata["warnings"],
                "No isolated production geometry was established.",
            ],
        }

    components = []
    for index, mask in enumerate(masks):
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        contour = max(contours, key=cv2.contourArea)
        polygon = (
            cv2.approxPolyDP(
                contour, 0.001 * cv2.arcLength(contour, True), True
            )
            .reshape(-1, 2)
            .tolist()
        )
        components.append(
            {
                "index": index,
                "mask": mask,
                "polygon": polygon,
                "bbox": _component_bbox(mask),
            }
        )

    # Build a global candidate/component score table, then assign one-to-one.
    # The previous local rule required >=65% of a component mask to lie inside
    # the AI bbox; slightly tight/offset bboxes therefore left valid visible
    # jersey panels as "Missing". Geometry still comes only from CV masks.
    pairs = []
    diagnostics = {}
    for component in components:
        for candidate_index, candidate in enumerate(candidates):
            score, evidence = _pair_score(
                component["mask"],
                component["bbox"],
                candidate,
                image.width,
                image.height,
            )
            diagnostics[f'{component["index"]}:{candidate_index}'] = evidence
            if score >= 0:
                pairs.append((score, component["index"], candidate_index))

    pairs.sort(key=lambda item: item[0], reverse=True)
    matched_components = {}
    used_candidates = set()
    for score, component_index, candidate_index in pairs:
        if component_index in matched_components or candidate_index in used_candidates:
            continue
        candidate = dict(candidates[candidate_index])
        evidence = diagnostics[f"{component_index}:{candidate_index}"]
        if score < 0.38:
            candidate["uncertain"] = True
        note = str(candidate.get("notes") or "").strip()
        match_note = (
            f"Deterministic boundary match score {score:.2f}; "
            f"IoU {evidence['bbox_iou']:.2f}; overlap {evidence['mask_overlap']:.2f}."
        )
        candidate["notes"] = (note + " " + match_note).strip()
        matched_components[component_index] = candidate
        used_candidates.add(candidate_index)

    assignments = []
    for component in components:
        assignments.append(
            {
                "mask": component["mask"],
                "polygon": component["polygon"],
                "candidate": matched_components.get(component["index"]),
                "boundary_evidence": "opencv_connected_component",
                "component_index": component["index"],
            }
        )

    # One semantic slot may have multiple components; none is silently discarded.
    types = [a["candidate"]["part_type"] for a in assignments if a["candidate"]]
    for assignment in assignments:
        if (
            assignment["candidate"]
            and types.count(assignment["candidate"]["part_type"]) > 1
        ):
            assignment["candidate"] = {
                **assignment["candidate"],
                "uncertain": True,
            }

    unmatched_candidates = len(candidates) - len(used_candidates)
    warnings = list(metadata.get("warnings", []))
    if unmatched_candidates:
        warnings.append(
            f"{unmatched_candidates} AI candidate(s) could not be tied safely to a deterministic boundary."
        )
    return assignments, {
        **metadata,
        "warnings": warnings,
        "matching": {
            "candidate_count": len(candidates),
            "component_count": len(components),
            "matched_count": len(matched_components),
            "unmatched_candidates": unmatched_candidates,
        },
    }
