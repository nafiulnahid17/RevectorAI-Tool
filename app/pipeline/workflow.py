"""Incremental production orchestration using the existing deterministic engine."""

from collections.abc import Callable

import numpy as np
from PIL import Image

from app.ai.contracts import SLOTS, PartSlot
from app.ai.prompts import MOCKUP_VERSION, mockup_runtime_prompt
from app.ai.image_quality import target_dimensions
from app.core.exceptions import EngineError
from app.errors.normalization import normalize
from app.models.project import State, now
from app.pipeline import geometry, hybrid_detection, segmentation


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
        mockup_size = target_dimensions(p.settings.image_quality, "4:3")
        signature = (
            p.source_hash,
            p.settings.image_quality,
            p.settings.mockup_background,
            list(mockup_size),
            p.settings.ai_workflow,
            e.ai_router.fingerprint(),
            MOCKUP_VERSION,
            hybrid_detection.MATCHING_VERSION,
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

        if p.settings.ai_workflow and (
            e.ai_router.configured() or e.ai_router.configuration_errors
        ):
            original = e.image(p.working_image)

            if cancelled():
                raise EngineError("JOB_CANCELLED", "Preparation cancelled")
            analysis, analysis_meta = e.ai_router.invoke("analyze_artwork", original)
            self.save_ai(pid, "analysis", analysis, analysis_meta)

            if cancelled():
                raise EngineError("JOB_CANCELLED", "Preparation cancelled")
            self.event(pid, "ENHANCING_ARTWORK")
            enhanced, enhancement_meta = e.ai_router.invoke(
                "enhance_artwork", original, mockup_size
            )
            enhancement_meta.update(
                requested_quality=p.settings.image_quality,
                requested_dimensions=list(mockup_size),
                actual_dimensions=list(enhanced.size),
            )
            self.save_ai(pid, "enhancement", enhanced, enhancement_meta)

            if cancelled():
                raise EngineError("JOB_CANCELLED", "Preparation cancelled")
            self.event(pid, "CREATING_PATTERN_MOCKUP")
            p = e.load(pid)
            enhanced = e.image(p.ai_assets["enhancement"])
            references = _reference_sheet([original, enhanced])
            prompt = mockup_runtime_prompt(
                p.settings.mockup_background,
                p.settings.image_quality,
                mockup_size,
                analysis,
            )
            mockup, mockup_meta = e.ai_router.invoke(
                "create_pattern_mockup", references, prompt, mockup_size
            )
            mockup_meta.update(
                mockup_generated=True,
                prompt_version=MOCKUP_VERSION,
                expected_parts=list(SLOTS),
                source_hash=p.source_hash,
                design_sources=["original", "enhancement"],
                requested_quality=p.settings.image_quality,
                requested_dimensions=list(mockup_size),
                actual_dimensions=list(mockup.size),
                selected_background=p.settings.mockup_background,
                inferred_surfaces_require_review=True,
            )
            self.save_ai(pid, "mockup", mockup, mockup_meta)

            p = e.load(pid)
            image = e.image(p.ai_assets["mockup"])
            if min(image.size) < 512:
                raise EngineError(
                    "MOCKUP_DIMENSIONS_MISMATCH",
                    "Mockup is below the minimum safe reference resolution",
                )
            if abs((image.width / image.height) / (4 / 3) - 1) > 0.05:
                raise EngineError(
                    "MOCKUP_DIMENSIONS_MISMATCH",
                    "AI returned an unexpected aspect ratio. Mockup Creation requires 4:3 landscape.",
                )

            if e.ai_router.supports_operation("verify_pattern_mockup"):
                if cancelled():
                    raise EngineError("JOB_CANCELLED", "Preparation cancelled")
                self.event(pid, "VERIFYING_PATTERN_MOCKUP")
                qc_sheet = _reference_sheet([original, enhanced, image])
                qc, qc_meta = e.ai_router.invoke("verify_pattern_mockup", qc_sheet)
                qc_meta.update(
                    source_hash=p.source_hash,
                    expected_parts=list(SLOTS),
                    prompt_version=MOCKUP_VERSION,
                )
                self.save_ai(pid, "mockup_qc", qc, qc_meta)
                if qc["serious_failure"] or not qc["pass_qc"]:
                    raise EngineError(
                        "MOCKUP_QC_FAILED",
                        "Generated production-layout reference failed independent QC; regenerate or review before Detect Parts",
                        status=409,
                        diagnostics={"qc": qc},
                    )

            if cancelled():
                raise EngineError("JOB_CANCELLED", "Preparation cancelled")
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
                    slot = p.slots[candidate["part_type"]]
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
                "expected_parts": list(SLOTS),
                "detected_candidates": candidates,
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
                    "AI is not configured: original source retained; manually classify exact CV/manual boundaries."
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

    def review(self, pid, decisions):
        e = self.engine
        with e.storage.lock(pid):
            p = e.load(pid)
            if set(decisions) != set(SLOTS):
                raise EngineError(
                    "PART_REVIEW_REQUIRED",
                    "Resolve all eight expected slots before production",
                    status=409,
                )
            ids = []
            for name, decision in decisions.items():
                if decision["status"] == "blank":
                    continue
                part = next(
                    (a for a in p.parts if a.part_id == decision.get("part_id")), None
                )
                if not part or part.type != name.lower() or not part.confirmed:
                    raise EngineError(
                        "PART_REVIEW_REQUIRED",
                        "Each nonblank slot requires a confirmed part of the matching type",
                        status=409,
                    )
                if part.part_id in ids:
                    raise EngineError(
                        "PART_REVIEW_REQUIRED",
                        "A component cannot fill more than one slot",
                        status=409,
                    )
                ids.append(part.part_id)
            if not ids:
                raise EngineError(
                    "PART_REVIEW_REQUIRED",
                    "Select at least one real production component",
                    status=409,
                )
            if set(ids) != {part.part_id for part in p.parts}:
                raise EngineError(
                    "PART_REVIEW_REQUIRED",
                    "Remove or classify extra components before confirming the review",
                    status=409,
                )
            for name, d in decisions.items():
                p.slots[name] = PartSlot(
                    part_type=name,
                    status="blank" if d["status"] == "blank" else "confirmed",
                    part_id=d.get("part_id") if d["status"] != "blank" else None,
                )
            p.ai_metadata["review"] = {"confirmed": True, "timestamp": now()}
            e.invalidate(p, "compose")
            p.state = State.SEGMENTED
            e.storage.save(p)
        return p

    def production(self, pid: str, params: dict, cancelled: Callable[[], bool]) -> dict:
        e = self.engine
        p = e.load(pid)
        if not p.ai_metadata.get("review", {}).get("confirmed"):
            raise EngineError(
                "PART_REVIEW_REQUIRED",
                "Confirm the eight-slot review before automatic production",
                status=409,
            )
        parts = e.selected(p, params)
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
                    next(
                        a for a in current.parts if a.part_id == part.part_id
                    ).error = None
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
                "Some parts failed. Completed part vectors are preserved; retry only the failed parts.",
                diagnostics={"parts": failures},
            )
        current = e.load(pid)
        if any(
            not a.vector or not a.cache.get("optimize") or a.error
            for a in current.parts
        ):
            raise EngineError(
                "PART_VECTOR_FAILED", "Other failed parts still require recovery"
            )
        e.run(pid, "compose", job_id=params.get("_job_id"), cancelled=cancelled)
        self.event(pid, "VALIDATING_VECTOR")
        try:
            result = e.run(
                pid, "validate", job_id=params.get("_job_id"), cancelled=cancelled
            )
        except EngineError:
            self.event(pid, "VALIDATION_FAILED")
            raise
        self.event(pid, "VALIDATION_PASSED")
        self.event(pid, "EXPORT_READY")
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
        image, meta = e.ai_router.invoke(
            "reconstruct_missing_part",
            e.image(p.ai_assets.get("mockup") or p.working_image),
            prompt,
            target_dimensions(p.settings.image_quality, "1:1"),
        )
        self.save_ai(pid, "missing-" + name.lower(), image, meta)
        masks, segmeta = segmentation.detect_masks(image, 0.001)
        isolated = segmeta["method"] != "whole_artboard_fallback" and len(masks) == 1
        if cancelled():
            raise EngineError("JOB_CANCELLED", "Missing-part reconstruction cancelled")
        with e.storage.lock(pid):
            p = e.load(pid)
            old = e.image(p.corrected_image)
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
