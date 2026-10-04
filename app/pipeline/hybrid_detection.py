"""AI classifies connected components; only deterministic pixel masks define geometry."""

import cv2
import numpy as np
from PIL import Image

from app.pipeline.segmentation import detect_masks


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
    assignments = []
    for i, mask in enumerate(masks):
        area = np.count_nonzero(mask)
        choices = []
        for c in candidates:
            x, y, w, h = c["candidate_bbox"]
            left, top = int(x * image.width), int(y * image.height)
            right, bottom = (
                min(image.width, int((x + w) * image.width)),
                min(image.height, int((y + h) * image.height)),
            )
            overlap = np.count_nonzero(mask[top:bottom, left:right]) / max(area, 1)
            if overlap >= 0.65:
                choices.append((overlap, c))
        choices.sort(key=lambda v: v[0], reverse=True)
        chosen = (
            choices[0][1]
            if choices and (len(choices) == 1 or choices[0][0] - choices[1][0] > 0.1)
            else None
        )
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        polygon = (
            cv2.approxPolyDP(contour, 0.001 * cv2.arcLength(contour, True), True)
            .reshape(-1, 2)
            .tolist()
        )
        assignments.append(
            {
                "mask": mask,
                "polygon": polygon,
                "candidate": chosen,
                "boundary_evidence": "opencv_connected_component",
                "component_index": i,
            }
        )
    # One semantic slot may have multiple components; none is silently discarded.
    types = [a["candidate"]["part_type"] for a in assignments if a["candidate"]]
    for a in assignments:
        if a["candidate"] and types.count(a["candidate"]["part_type"]) > 1:
            a["candidate"] = {**a["candidate"], "uncertain": True}
    return assignments, metadata
