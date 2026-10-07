"""Incremental production orchestration using the existing deterministic engine."""

from collections.abc import Callable

import numpy as np
from PIL import Image

from app.ai.contracts import SLOTS, PartSlot
from app.ai.prompts import MASTER_MOCKUP_COMMAND, MOCKUP_VERSION, mockup_runtime_prompt
from app.ai.image_quality import (
    nearest_supported_ratio,
    preset_quality_label,
    preset_target_dimensions,
)
from app.core.exceptions import EngineError
from app.errors.normalization import normalize
from app.models.project import State, now
from app.pipeline import geometry, hybrid_detection, segmentation


PREPARE_VERSION = "prepare/3.0-dynamic-selective-production"
BODY_WIDTH_MM = 558.8
BODY_HEIGHT_MM = 787.4

# Front/back are client-locked. Other values are editable starting production
# canvases and are applied only when the operator has not supplied dimensions.
PART_DIMENSION_DEFAULTS_MM = {
    "front_body": (558.8, 787.4),
    "back_body": (558.8, 787.4),
    "left_sleeve": (330.2, 457.2),
    "right_sleeve": (330.2, 457.2),
    "front_collar": (254.0, 127.0),
    "back_collar": (254.0, 127.0),
    "top_trim": (558.8, 76.2),
    "bottom_trim": (558.8, 76.2),
    "trim": (558.8, 76.2),
    "left_cuff": (254.0, 101.6),
    "right_cuff": (254.0, 101.6),
    "left_shoulder": (279.4, 203.2),
    "right_shoulder": (279.4, 203.2),
}


def _normalize_generated_size(
    image: Image.Image, target: tuple[int, int]
) -> Image.Image:
    """Store predictable preset-sized rasters after provider generation.

    Providers choose their own native pixel dimensions. ReVector requests the
    nearest OpenRouter resolution tier, then normalizes a correctly shaped
    result to the user-selected workspace target without inventing geometry.
    """
    target_width, target_height = target
    current_ratio = image.width / max(image.height, 1)
    target_ratio = target_width / max(target_height, 1)
    if abs(current_ratio / target_ratio - 1) > 0.05:
        return image
    if image.size == target:
        return image
    return image.resize(target, Image.Resampling.LANCZOS)


def _reference_sheet(images: list[Image.Image]) -> Image.Image:
    """Bounded visual evidence sheet. It never becomes a production artifact."""
    cells = []
    for image in images:
        cell = image.convert("RGB").copy()
        cell.thumbnail((900, 900))
        cells.append(cell)
    gap = 24
    width = sum(cell.width for cell in cells) + gap * max(0, len(cells) - 1)
    height = max(cell.height for cell in cells)
    sheet = Image.new("RGB", (width, height), "black")
    x = 0
    for cell in cells:
        y = (height - cell.height) // 2
        sheet.paste(cell, (x, y))
        x += cell.width + gap
    return sheet


class ProductionWorkflow:
    def __init__(self, engine):
        self.engine = engine

    def event(self, pid, name, part_id=None, **metadata):
        with self.engine.storage.lock(pid):
            p = self.engine.load(pid)
            p.events.append(
                {"event": name, "part_id": part_id, "timestamp": now(), **metadata}
            )
            p.events = p.events[-300:]
            if part_id:
                part = next((v for v in p.parts if v.part_id == part_id), None)
                if part:
                    part.processing_state = name
            self.engine.storage.save(p)

    def save_ai(self, pid, operation, value, metadata):
        with self.engine.storage.lock(pid):
            p = self.engine.load(pid)
            if isinstance(value, Image.Image):
                p.ai_assets[operation] = self.engine.put_image(
                    self.engine.key(p, "working/ai-" + operation + ".png"), value
                )
            p.ai_metadata[operation] = {
                **metadata,
                "created_at": now(),
                "intermediate_raster": isinstance(value, Image.Image),
            }
            p.usage["ai_calls"] += metadata["attempt_count"]
            p.usage["processing_ms"] += metadata.get("duration_ms", 0)
            if isinstance(value, dict):
                p.ai_metadata[operation]["result"] = value
            self.engine.storage.save(p)

    def prepare(self, pid: str, params: dict, cancelled: Callable[[], bool]) -> dict:
        e = self.engine
        p = e.load(pid)
        mockup_size = (p.settings.mockup_width, p.settings.mockup_height)
        signature = (
            p.source_hash,
            p.settings.mockup_width,
            p.settings.mockup_height,
            p.settings.ai_workflow,
            e.ai_router.fingerprint(),
            MOCKUP_VERSION,
            hybrid_detection.MATCHING_VERSION,
            PREPARE_VERSION,
        )
        if (
            p.ai_metadata.get("prepare_signature") == list(signature)
            and p.corrected_image
            and e.storage.exists(p.corrected_image)
            and e.file_hash(p.corrected_image)
            == p.ai_metadata.get("prepare_reference_sha256")
            and p.state == State.PART_REVIEW_READY
        ):
            return {
                "cached": True,
                "status": "PART_REVIEW_READY",
                "detected_parts": len(p.parts),
            }

        self.event(pid, "ANALYZING_ARTWORK")
        e.run(pid, "analyze", job_id=params.get("_job_id"), cancelled=cancelled)
        p = e.load(pid)

        # Stable 699ac010 preparation path:
        # Original -> Analyze -> Enhance -> Enhanced-only Mockup -> Identify -> CV refine.
        if p.settings.ai_workflow and (
            e.ai_router.configured() or e.ai_router.configuration_errors
        ):
            for operation, event, method in [
                ("analysis", "ANALYZING_ARTWORK", "analyze_artwork"),
                ("enhancement", "ENHANCING_ARTWORK", "enhance_artwork"),
                ("mockup", "CREATING_PATTERN_MOCKUP", "create_pattern_mockup"),
            ]:
                if cancelled():
                    raise EngineError("JOB_CANCELLED", "Preparation cancelled")
                self.event(pid, event)
                p = e.load(pid)
                source = (
                    p.ai_assets.get("enhancement")
                    if operation == "mockup"
                    else p.working_image
                )
                image = e.image(source)
                size = mockup_size
                analysis_result = p.ai_metadata.get("analysis", {}).get("result", {})
                args = (
                    (image,)
                    if operation == "analysis"
                    else (
                        image,
                        mockup_runtime_prompt(
                            p.settings.mockup_background,
                            preset_quality_label(p.settings.preset),
                            size,
                            analysis_result,
                        ),
                        size,
                    )
                    if operation == "mockup"
                    else (image, size)
                )
                value, meta = e.ai_router.invoke(method, *args)
                if operation in {"enhancement", "mockup"}:
                    value = _normalize_generated_size(value, size)
                if operation == "mockup":
                    meta.update(
                        mockup_generated=True,
                        prompt_version=MOCKUP_VERSION,
                        component_policy="dynamic_visible_components",
                        standard_slot_preset=list(SLOTS),
                        source_hash=p.source_hash,
                        requested_dimensions=list(size),
                        requested_aspect_ratio=nearest_supported_ratio(size),
                        actual_dimensions=list(value.size),
                        inferred_surfaces_require_review=True,
                    )
                self.save_ai(pid, operation, value, meta)

            p = e.load(pid)
            image = e.image(p.ai_assets["mockup"])
            if max(image.size) < 1024:
                raise EngineError(
                    "MOCKUP_DIMENSIONS_MISMATCH",
                    "Mockup is below the minimum reference resolution",
                )
            requested_ratio_name = nearest_supported_ratio(mockup_size)
            requested_ratio = {
                "1:1": 1.0,
                "4:3": 4 / 3,
                "3:4": 3 / 4,
                "16:9": 16 / 9,
                "9:16": 9 / 16,
            }[requested_ratio_name]
            if abs(image.width / image.height / requested_ratio - 1) > 0.05:
                raise EngineError(
                    "MOCKUP_DIMENSIONS_MISMATCH",
                    "AI returned an unexpected provider-supported aspect ratio. Retry the provider.",
                    diagnostics={
                        "requested_dimensions": list(mockup_size),
                        "requested_aspect_ratio": requested_ratio_name,
                        "actual_dimensions": list(image.size),
                    },
                )

            self.event(pid, "IDENTIFYING_PARTS")
            candidates, identify_meta = e.ai_router.invoke("identify_parts", image)
            self.save_ai(pid, "identification", None, identify_meta)
        else:
            image = e.image(p.working_image)
            candidates = []

        if cancelled():
            raise EngineError("JOB_CANCELLED", "Preparation cancelled")
        self.event(pid, "REFINING_PART_BOUNDARIES")
        assignments, meta = hybrid_detection.refine(
            image, candidates, min(p.settings.segment_min_area_ratio, 0.0005)
        )
        with e.storage.lock(pid):
            p = e.load(pid)
            e.invalidate(p, "segment")
            p.corrected_image = e.put_image(
                e.key(p, "working/detection-reference.png"), image
            )
            _, p.geometry = geometry.correct(image, None, False)
            p.slots = {name: PartSlot(part_type=name) for name in SLOTS}
            for assignment in assignments:
                candidate = assignment["candidate"]
                fields = {
                    "polygon": assignment["polygon"],
                    "source": "engine_refined",
                    "name": "Unclassified component",
                }
                if candidate:
                    fields.update(
                        type=candidate["part_type"].lower(),
                        name=candidate["part_type"].replace("_", " ").title(),
                        confidence=candidate["confidence"],
                        ai_confidence=candidate["confidence"],
                    )
                part = e._create_part(p, assignment["mask"], fields)
                if candidate:
                    slot = p.slots.get(candidate["part_type"])
                    if slot:
                        if slot.part_id:
                            slot.status = "uncertain"
                        else:
                            slot.part_id = part.part_id
                            slot.status = (
                                "uncertain" if candidate["uncertain"] else "detected"
                            )
                            slot.ai_confidence = candidate["confidence"]
                            slot.candidate_bbox = list(candidate["candidate_bbox"])
                        slot.notes.append(candidate["notes"])
                p.parts.append(part)

            if candidates:
                p.warnings.append(
                    "AI reference fidelity is not certified. Review colors, branding, unseen surfaces and seam continuity before production."
                )
            p.ai_metadata["detection"] = {
                "standard_slot_preset": list(SLOTS),
                "component_policy": "dynamic_visible_components",
                "detected_candidates": candidates,
                "detected_component_count": len(p.parts),
                "missing_parts": [
                    key for key, value in p.slots.items() if value.status == "missing"
                ],
                "uncertain_parts": [
                    key for key, value in p.slots.items() if value.status == "uncertain"
                ],
                "geometry_source": "opencv",
                "source": "mockup" if p.ai_assets.get("mockup") else "original",
                "segmentation": meta,
                "created_at": now(),
            }
            if not candidates:
                p.warnings.append(
                    "AI semantic labels were unavailable or inconclusive. Deterministic CV boundaries are preserved; manually classify the detected components."
                )
            p.ai_metadata["prepare_signature"] = list(signature)
            p.ai_metadata["prepare_reference_sha256"] = e.file_hash(p.corrected_image)
            p.state = State.PART_REVIEW_READY
            self.engine.storage.save(p)
        self.event(pid, "PART_REVIEW_READY")
        return {
            "status": "PART_REVIEW_READY",
            "detected_parts": len(p.parts),
            "slots": {key: value.model_dump() for key, value in p.slots.items()},
        }

    def _apply_client_dimensions(self, part) -> None:
        """Apply production defaults without overriding operator sizes.

        Front/back body size is an explicit client contract and is always locked
        to 22 x 31 inches. Other known parts get editable defaults only when no
        physical size has been supplied.
        """
        if part.type in {"front_body", "back_body"}:
            part.physical_width_mm = BODY_WIDTH_MM
            part.physical_height_mm = BODY_HEIGHT_MM
            return
        default = PART_DIMENSION_DEFAULTS_MM.get(part.type)
        if default and part.physical_width_mm is None:
            part.physical_width_mm, part.physical_height_mm = default

    def review(self, pid, decisions, selected_part_ids=None):
        e = self.engine
        with e.storage.lock(pid):
            p = e.load(pid)

            decision_ids = [
                decision.get("part_id")
                for decision in (decisions or {}).values()
                if decision.get("status") == "confirmed" and decision.get("part_id")
            ]
            ids = list(selected_part_ids or decision_ids)
            if not ids:
                raise EngineError(
                    "PART_REVIEW_REQUIRED",
                    "Select at least one detected component for vectorization",
                    status=409,
                )
            if len(ids) != len(set(ids)):
                raise EngineError(
                    "PART_REVIEW_REQUIRED",
                    "A component can be selected only once",
                    status=409,
                )

            selected = []
            for part_id in ids:
                part = next((a for a in p.parts if a.part_id == part_id), None)
                if not part:
                    raise EngineError(
                        "PART_NOT_FOUND",
                        "Selected production component no longer exists",
                        status=404,
                    )
                if not part.confirmed:
                    raise EngineError(
                        "PART_REVIEW_REQUIRED",
                        "Every selected component must be reviewed and confirmed before vectorization",
                        status=409,
                    )
                self._apply_client_dimensions(part)
                part.processing_state = "CONFIRMED"
                selected.append(part)

            # Slot decisions are now optional compatibility metadata. They never
            # gate dynamic/extra components or force all eight standard slots.
            for name, decision in (decisions or {}).items():
                if name not in p.slots:
                    continue
                if decision.get("status") == "blank":
                    p.slots[name] = PartSlot(part_type=name, status="blank")
                    continue
                part_id = decision.get("part_id")
                part = next((a for a in p.parts if a.part_id == part_id), None)
                if part and part.confirmed and part.type == name.lower():
                    p.slots[name] = PartSlot(
                        part_type=name,
                        status="confirmed",
                        part_id=part.part_id,
                        ai_confidence=part.ai_confidence,
                    )

            p.ai_metadata["review"] = {
                "confirmed": True,
                "selected_part_ids": ids,
                "selected_count": len(ids),
                "timestamp": now(),
                "policy": "selective_dynamic_components",
            }
            e.invalidate(p, "compose")
            p.state = State.SEGMENTED
            e.storage.save(p)
        return p

    def production(self, pid: str, params: dict, cancelled: Callable[[], bool]) -> dict:
        e = self.engine
        p = e.load(pid)
        review = p.ai_metadata.get("review", {})
        if not review.get("confirmed"):
            raise EngineError(
                "PART_REVIEW_REQUIRED",
                "Confirm at least one selected component before vector production",
                status=409,
            )

        requested_ids = (
            [params["part_id"]]
            if params.get("part_id")
            else params.get("part_ids") or review.get("selected_part_ids")
        )
        if not requested_ids:
            raise EngineError(
                "PART_REVIEW_REQUIRED",
                "No components are selected for vector production",
                status=409,
            )
        reviewed = set(review.get("selected_part_ids") or [])
        if reviewed and not set(requested_ids).issubset(reviewed):
            raise EngineError(
                "PART_REVIEW_REQUIRED",
                "Production selection contains a component that was not confirmed in the current review",
                status=409,
            )

        parts = e.selected(p, {"part_ids": requested_ids})
        failures = []
        for part in parts:
            if cancelled():
                raise EngineError(
                    "JOB_CANCELLED",
                    "Production cancelled; completed parts remain stored",
                )
            try:
                for stage, event in [
                    ("reconstruct", "RECONSTRUCTING_PART"),
                    ("vectorize", "TRACING_VECTOR"),
                    ("optimize", "OPTIMIZING_VECTOR"),
                ]:
                    self.event(pid, event, part.part_id)
                    e.run(
                        pid,
                        stage,
                        {
                            "part_id": part.part_id,
                            "fallback_trace": params.get("fallback_trace", False),
                        },
                        job_id=params.get("_job_id"),
                        cancelled=cancelled,
                    )
                self.event(pid, "VECTOR_READY", part.part_id)
                with e.storage.lock(pid):
                    current = e.load(pid)
                    item = next(a for a in current.parts if a.part_id == part.part_id)
                    item.error = None
                    item.processing_state = "VECTOR_READY"
                    e.storage.save(current)
            except EngineError as exc:
                if exc.code == "JOB_CANCELLED":
                    raise
                with e.storage.lock(pid):
                    current = e.load(pid)
                    item = next(a for a in current.parts if a.part_id == part.part_id)
                    item.error = normalize(
                        exc.code,
                        exc.message,
                        exc.recoverable,
                        phase="vectorize",
                        project_id=pid,
                        part_id=part.part_id,
                        part_type=part.type,
                    )
                    item.error.update(
                        trace_engine=exc.diagnostics.get("trace_engine")
                        or item.metrics.get("backend"),
                        fallback_attempted=params.get("fallback_trace", False)
                        or exc.diagnostics.get("fallback_attempted", False)
                        or bool(exc.diagnostics.get("attempts")),
                        validation_errors=exc.diagnostics.get("validation_errors", []),
                    )
                    item.processing_state = "FAILED"
                    failures.append(item.error)
                    e.storage.save(current)

        if failures:
            raise EngineError(
                "PART_VECTOR_FAILED",
                "Some selected parts failed. Completed vectors are preserved; retry, review or exclude the failed part.",
                diagnostics={"parts": failures, "selected_part_ids": list(requested_ids)},
            )

        current = e.load(pid)
        selected_now = [
            a for a in current.parts if a.part_id in set(requested_ids)
        ]
        if any(
            not a.vector or not a.cache.get("optimize") or a.error
            for a in selected_now
        ):
            raise EngineError(
                "PART_VECTOR_FAILED",
                "A selected component is not ready for validation",
            )

        selection = {"part_ids": list(requested_ids)}
        e.run(
            pid,
            "compose",
            selection,
            job_id=params.get("_job_id"),
            cancelled=cancelled,
        )
        self.event(pid, "VALIDATING_VECTOR")
        try:
            result = e.run(
                pid,
                "validate",
                selection,
                job_id=params.get("_job_id"),
                cancelled=cancelled,
            )
        except EngineError:
            self.event(pid, "VALIDATION_FAILED")
            raise

        with e.storage.lock(pid):
            current = e.load(pid)
            for item in current.parts:
                if item.part_id in set(requested_ids):
                    item.processing_state = "EXPORT_READY"
            e.storage.save(current)
        self.event(pid, "VALIDATION_PASSED")
        self.event(pid, "EXPORT_READY", selected_part_ids=list(requested_ids))
        return result

    def missing(self, pid: str, params: dict, cancelled: Callable[[], bool]) -> dict:
        e = self.engine
        p = e.load(pid)
        name = params["slot"]
        if p.slots[name].status not in {"missing", "blank"} or any(
            a.type == name.lower() for a in p.parts
        ):
            raise EngineError(
                "PART_ALREADY_EXISTS",
                "Remove the existing component before replacing this slot",
                status=409,
            )
        self.event(pid, "RECONSTRUCTING_PART", slot=name)
        prompt = f"Reconstruct only {name} as one detached flat jersey component on {p.settings.mockup_background} background. Preserve observed colors and branding from this reference. Do not invent unknown logos, text or numbers. Unseen artwork is an inferred proposal requiring review. No other pieces."
        missing_size = preset_target_dimensions(p.settings.preset, "1:1")
        image, meta = e.ai_router.invoke(
            "reconstruct_missing_part",
            e.image(p.ai_assets.get("mockup") or p.working_image),
            prompt,
            missing_size,
        )
        image = _normalize_generated_size(image, missing_size)
        meta.update(
            requested_quality=preset_quality_label(p.settings.preset),
            workspace_preset=p.settings.preset,
            requested_dimensions=list(missing_size),
            actual_dimensions=list(image.size),
        )
        self.save_ai(pid, "missing-" + name.lower(), image, meta)
        masks, segmeta = segmentation.detect_masks(image, 0.001)
        isolated = segmeta["method"] != "whole_artboard_fallback" and len(masks) == 1
        if cancelled():
            raise EngineError("JOB_CANCELLED", "Missing-part reconstruction cancelled")
        with e.storage.lock(pid):
            p = e.load(pid)
            base_key = (
                p.corrected_image
                or p.ai_assets.get("mockup")
                or p.working_image
            )
            if not base_key:
                raise EngineError(
                    "STAGE_PREREQUISITE",
                    "A real project reference is required before reconstructing a missing part.",
                    status=409,
                )
            old = e.image(base_key)
            canvas = Image.new(
                "RGBA",
                (old.width + image.width + 32, max(old.height, image.height)),
                (0, 0, 0, 255),
            )
            if canvas.width * canvas.height > e.settings.max_pixels:
                raise EngineError(
                    "IMAGE_TOO_LARGE",
                    "Expanded detection reference exceeds pixel limit",
                )
            canvas.paste(old, (0, 0))
            canvas.paste(image, (old.width + 32, 0))
            e.invalidate(p, "compose")
            p.corrected_image = e.put_image(
                e.key(p, "working/detection-reference.png"), canvas
            )
            _, p.geometry = geometry.correct(canvas, None, False)
            if not isolated:
                p.ai_metadata.setdefault("missing_proposals", {})[name] = {
                    "bbox": [old.width + 32, 0, image.width, image.height],
                    "reference": p.ai_assets["missing-" + name.lower()],
                    "status": "requires_manual_boundary",
                }
                p.ai_metadata.pop("review", None)
                p.state = State.PART_REVIEW_READY
                e.storage.save(p)
                raise EngineError(
                    "DETECTION_ERROR",
                    "AI proposal has no single isolated boundary. The proposal is preserved on the detection canvas for manual selection.",
                )
            full = np.zeros((canvas.height, canvas.width), np.uint8)
            full[: image.height, old.width + 32 :] = masks[0]
            part = e._create_part(
                p,
                full,
                {
                    "type": name.lower(),
                    "name": name.replace("_", " ").title(),
                    "source": "ai_reconstructed",
                },
            )
            part.warnings.append(
                "AI inferred missing part. Review identity, proportions and seams before confirmation."
            )
            p.parts.append(part)
            p.slots[name] = PartSlot(
                part_type=name, status="ai_reconstructed", part_id=part.part_id
            )
            p.ai_metadata.setdefault("missing_reconstructions", []).append(
                {**meta, "slot": name, "part_id": part.part_id, "timestamp": now()}
            )
            p.ai_metadata.pop("review", None)
            p.state = State.PART_REVIEW_READY
            e.storage.save(p)
        self.event(pid, "PART_REVIEW_READY", part.part_id)
        return {"part_id": part.part_id, "status": "ai_reconstructed"}

    def run(
        self,
        pid: str,
        stage: str,
        params: dict,
        job_id: str | None,
        cancelled: Callable[[], bool],
    ) -> dict:
        params = {**params, "_job_id": job_id}
        try:
            return (
                self.prepare
                if stage == "prepare"
                else self.missing
                if stage == "ai-missing"
                else self.production
            )(pid, params, cancelled)
        except Exception as unexpected:  # noqa: BLE001 — normalize unexpected job failures
            exc = (
                unexpected
                if isinstance(unexpected, EngineError)
                else EngineError(
                    "PROCESSING_FAILED",
                    "Unexpected orchestration failure; inspect safe error details",
                    False,
                    500,
                )
            )
            with self.engine.storage.lock(pid):
                p = self.engine.load(pid)
                failure = (exc.diagnostics.get("parts") or [{}])[0]
                exc.normalized = normalize(
                    exc.code,
                    exc.message,
                    exc.recoverable,
                    phase=stage,
                    project_id=pid,
                    job_id=job_id,
                    part_id=params.get("part_id") or failure.get("part_id"),
                    part_type=failure.get("part_type"),
                )
                exc.normalized["diagnostics"] = exc.diagnostics
                p.usage["ai_calls"] += sum(
                    bool(a.get("dispatched", "provider" in a))
                    for a in exc.diagnostics.get("attempts", [])
                )
                p.error = exc.normalized
                p.error_history.append(exc.normalized)
                p.error_history = p.error_history[-50:]
                p.state = State.FAILED
                self.engine.storage.save(p)
            raise exc
