"""Reusable staged engine. Each mutation is locked and each transformation is recorded."""
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import time
import zipfile
from xml.etree import ElementTree as ET
from defusedxml import ElementTree as SafeET
import numpy as np
from PIL import Image
from app.core.config import Settings, AISettings
from app.core.exceptions import EngineError
from app.core.logging import log_event
from app.models.project import Project, Part, ProcessingSettings, State, now
from app.storage.local import LocalStorage
from app.pipeline import ingest, quality, geometry, segmentation, reconstruction, decomposition, vectorization
from app.pipeline import export as exporter
from app.pipeline import colors
from app.providers.ocr import TesseractOCR
from app.vector.simplify import optimize
from app.vector.svg_composer import compose, diagnostic
from app.validators.svg_validator import validate_svg
from app.validators.visual_diff import render_svg, compare

PRODUCTION_VECTOR_VERSION = "illustrator-production/1.0"
STAGES = ["analyze", "correct-geometry", "segment", "reconstruct", "vectorize", "optimize", "compose", "validate"]
STATES = {
    "analyze": (State.ANALYZING, State.ANALYZED),
    "correct-geometry": (State.ANALYZED, State.GEOMETRY_CORRECTED),
    "segment": (State.SEGMENTING, State.SEGMENTED),
    "reconstruct": (State.RECONSTRUCTING, State.RECONSTRUCTED),
    "vectorize": (State.VECTORIZING, State.VECTORIZED),
    "optimize": (State.OPTIMIZING, State.OPTIMIZED),
    "compose": (State.COMPOSING, State.COMPOSED),
    "validate": (State.VALIDATING, State.READY),
}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Engine:
    def __init__(self, settings: Settings, *, vision_provider=None, reconstruction_provider=None,
                 segmentation_provider=None, ocr_provider=None, ai_router=None, error_ai_router=None):
        self.settings = settings
        from app.ai.router import AIRouter
        from app.ai.quota import FallbackQuota
        from app.pipeline.workflow import ProductionWorkflow
        if settings.storage_backend != "local":
            raise EngineError("NOT_IMPLEMENTED", "Automatic R2 project orchestration is not implemented; use the explicit R2Storage adapter")
        self.storage = LocalStorage(settings.data_dir)
        ai_settings = AISettings()
        self.fallback_quota = FallbackQuota(
            self.storage.root / "_system" / "fallback-ai.sqlite3",
            ai_settings.fallback_max_calls,
            ai_settings.fallback_window_hours,
            ai_settings.fallback_limit_scope,
        )
        self.ai_router = ai_router or AIRouter(ai_settings, quota=self.fallback_quota)
        self.error_ai_router = error_ai_router or AIRouter(
            ai_settings, error_mode=True, quota=self.fallback_quota
        )
        self.workflow = ProductionWorkflow(self)
        self.vision_provider = vision_provider
        self.reconstruction_provider = reconstruction_provider
        self.segmentation_provider = segmentation_provider
        self.ocr_provider = ocr_provider or (TesseractOCR(settings.tool_timeout_seconds) if settings.ocr_provider == "tesseract" else None)
        if settings.segmentation_provider != "opencv" and segmentation_provider is None:
            raise EngineError("NOT_IMPLEMENTED", "Configured segmentation provider needs an injected adapter")

    def create(self, **fields) -> Project:
        project = Project(**fields)
        self.storage.save(project)
        return project

    def load(self, project_id: str) -> Project:
        p=self.storage.load(project_id)
        p.exports={k:v for k,v in p.exports.items() if k.startswith('selected_zip_')} if p.stage_metadata.get('export_policy')=='parts_only_v1' else {}
        return p

    def key(self, project: Project, suffix: str) -> str:
        return f"projects/{project.project_id}/{suffix}"

    def image(self, key: str) -> Image.Image:
        image = Image.open(BytesIO(self.storage.get(key))).convert("RGBA")
        image.load()
        return image

    def put_image(self, key: str, image: Image.Image) -> str:
        self.storage.put(key, ingest.png_bytes(image))
        return key

    def file_hash(self, key: str | None) -> str | None:
        return hashlib.sha256(self.storage.get(key)).hexdigest() if key else None

    def upload(self, project_id: str, data: bytes, filename: str, mime: str | None = None) -> Project:
        image, meta = ingest.ingest(data, filename, mime, self.settings.max_upload_bytes, self.settings.max_pixels)
        with self.storage.lock(project_id):
            p = self.load(project_id)
            self.invalidate(p, "analyze")
            p.ai_assets, p.ai_metadata = {}, {}
            from app.ai.contracts import SLOTS, PartSlot
            p.slots = {name:PartSlot(part_type=name) for name in SLOTS}
            p.events, p.error_history, p.assistant_sessions = [], [], {}
            p.events.append({'event':'UPLOAD_RECEIVED','timestamp':now()})
            p.source_hash, p.source_metadata = meta["sha256"], meta
            suffix = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[meta["format"]]
            p.source_file = self.key(p, f"source/{p.source_hash}.{suffix}")
            self.storage.put(p.source_file, data)
            p.working_image = self.put_image(self.key(p, "working/normalized.png"), image)
            thumb = image.copy()
            thumb.thumbnail((400, 400))
            p.thumbnail = self.put_image(self.key(p, "previews/thumbnail.png"), thumb)
            p.state, p.error = State.UPLOADED, None
            p.usage["input_megapixels"] = image.width * image.height / 1e6
            p.stage_metadata["ingest"] = {**meta, "timestamp": now(), "source_resolution_preserved": True}
            self.storage.save(p)
            return p

    def invalidate(self, p: Project, stage: str, affected: set[str] | None = None):
        """Invalidate published artifacts and only the affected component's expensive intermediates."""
        index = STAGES.index(stage)
        for name in STAGES[index:]:
            p.cache.pop(name, None)
            p.stage_metadata.pop(name, None)
        if index <= 0:
            p.analysis = {}
        if index <= 1:
            p.geometry, p.corrected_image = {}, None
        if index <= 2:
            p.parts = []
            from app.ai.contracts import SLOTS, PartSlot
            p.slots={name:PartSlot(part_type=name) for name in SLOTS}
            p.ai_metadata.pop('review',None)
        for part in p.parts:
            if affected is not None and part.part_id not in affected:
                continue
            for name in ["reconstruct", "vectorize", "optimize"]:
                if STAGES.index(name) >= index:
                    part.cache.pop(name, None)
            if index <= 3:
                part.clean_reference = part.vectorization_source = None
                part.palette, part.ocr_results, part.decomposition = [], [], {}
            if index <= 4:
                part.vector, part.metrics = None, {}
        p.master_svg, p.validation, p.previews, p.exports = None, None, {}, {}
        for part in p.parts:
            part.validation = None
            part.previews = {}
        for part in p.parts:
            part.exports = {}
        p.error = None

    def update_settings(self, project_id: str, changes: dict) -> Project:
        with self.storage.lock(project_id):
            p = self.load(project_id)
            old = p.settings.model_dump()
            try:
                updated = ProcessingSettings.model_validate({**old, **changes})
            except ValueError as exc:
                raise EngineError("INVALID_SETTINGS", "Processing settings are invalid or physical geometry lacks calibration") from exc
            changed = {k for k, v in updated.model_dump().items() if old[k] != v}
            if changed:
                if changed & {"segment_min_area_ratio","mockup_width","mockup_height","ai_workflow"}:
                    stage = "segment"
                elif changed & {"max_colors", "delta_e", "noise_reduction", "preserve_original_colors", "ocr", "text_mode"}:
                    stage = "reconstruct"
                elif changed & {"preset", "vector_mode", "min_region_area", "gradients", "allow_contour_fallback", "max_trace_dimension"}:
                    stage = "vectorize"
                else:
                    stage = "compose"
                self.invalidate(p, stage)
                p.state = {"segment": State.GEOMETRY_CORRECTED, "reconstruct": State.SEGMENTED,
                           "vectorize": State.RECONSTRUCTED, "compose": State.OPTIMIZED}[stage]
                p.manual_changes.append({"action": "settings", "changed": changes, "timestamp": now()})
            p.settings = updated
            self.storage.save(p)
            return p

    def _cached(self, p: Project, stage: str, signature: str) -> bool:
        cache = p.cache.get(stage, {})
        if cache.get("signature") != signature:
            return False
        return all(self.storage.exists(k) and self.file_hash(k) == v for k, v in cache.get("artifacts", {}).items())

    def run(self, project_id: str, stage: str, params: dict | None = None, *, job_id: str | None = None,
            cancelled=lambda: False) -> dict:
        params = params or {}
        if stage in {'prepare','production','recover-part','ai-missing'}:
            return self.workflow.run(project_id,stage,params,job_id,cancelled)
        if stage not in [*STAGES, "export"]:
            raise EngineError("NOT_IMPLEMENTED", "Unknown processing stage")
        with self.storage.lock(project_id):
            p = self.load(project_id)
            if not p.source_file:
                raise EngineError("STAGE_PREREQUISITE", "Upload a source image first", status=409)
            if cancelled():
                raise EngineError("JOB_CANCELLED", "Processing was cancelled")
            start = time.perf_counter()
            previous_state = p.state
            if stage in STATES:
                p.state = STATES[stage][0]
                p.error = None
                self.storage.save(p)
            try:
                method = getattr(self, "stage_" + stage.replace("-", "_"))
                result = method(p, params, cancelled)
                if cancelled():
                    raise EngineError("JOB_CANCELLED", "Processing cancelled at stage boundary")
                if stage in STATES:
                    # Cached earlier stages do not demote an already-ready project.
                    p.state = previous_state if result.get("cached") else STATES[stage][1]
                elapsed = round((time.perf_counter() - start) * 1000, 3)
                p.stage_metadata[stage] = {**result, "duration_ms": elapsed, "timestamp": now(), "job_id": job_id}
                p.usage["processing_ms"] += elapsed
                self.storage.save(p)
                log_event("stage_complete", project_id=project_id, job_id=job_id, stage=stage,
                          duration_ms=elapsed, status=p.state)
                return {"project_id": project_id, "stage": stage, "status": str(p.state), **result}
            except Exception as exc:
                error = exc if isinstance(exc, EngineError) else EngineError("PROCESSING_FAILED", "Unexpected processing failure; see server logs", False, 500)
                if not isinstance(exc, EngineError):
                    import logging
                    logging.getLogger("revector").error("unexpected_stage_failure project=%s stage=%s", project_id, stage)
                from app.errors.normalization import normalize
                from app.errors.service import credentials
                error.normalized = normalize(error.code,error.message,error.recoverable,phase=stage,project_id=project_id,job_id=job_id,part_id=params.get('part_id'),secrets=credentials(self))
                p.error = error.as_dict()
                p.error_history.append(p.error)
                p.error_history = p.error_history[-50:]
                # Export failures preserve validated, usable SVG and any successful formats.
                integrity_failure = error.code in {"STALE_VALIDATION", "SVG_VALIDATION_FAILED", "RASTER_FOUND_IN_TRUE_VECTOR", "SVG_RENDER_FAILED"}
                p.state = previous_state if stage == "export" and not integrity_failure else State.FAILED
                if stage == "export" and integrity_failure:
                    p.validation, p.previews, p.exports = None, {}, {}
                if stage == "validate":
                    p.exports = {}
                self.storage.save(p)
                log_event("stage_failed", project_id=project_id, job_id=job_id, stage=stage, code=error.code)
                raise error

    def require(self, predicate: bool, message: str):
        if not predicate:
            raise EngineError("STAGE_PREREQUISITE", message, status=409)

    def record_cache(self, p: Project, stage: str, signature: str, artifacts: list[str] | None = None):
        p.cache[stage] = {"signature": signature, "artifacts": {k: self.file_hash(k) for k in artifacts or []}}

    def stage_analyze(self, p, params, cancelled):
        signature = digest([p.engine_version, self.file_hash(p.working_image)])
        if self._cached(p, "analyze", signature):
            return {"cached": True, "analysis": p.analysis}
        self.invalidate(p, "analyze")
        p.analysis = quality.analyze(self.image(p.working_image))
        if self.vision_provider:
            p.usage["ai_calls"] += 1
            try:
                p.analysis["provider_analysis"] = self.vision_provider.analyze_image(self.image(p.working_image))
            except Exception:
                p.warnings.append("AI_PROVIDER_UNAVAILABLE: vision failed; deterministic analysis retained.")
        self.record_cache(p, "analyze", signature)
        return {"analysis": p.analysis}

    def stage_correct_geometry(self, p, params, cancelled):
        self.require(bool(p.analysis), "Analyze the source first")
        source_key=p.ai_assets.get("mockup") or p.working_image
        signature = digest([self.file_hash(source_key), params])
        if self._cached(p, "correct-geometry", signature):
            return {"cached": True, "geometry": p.geometry}
        image, meta = geometry.correct(self.image(source_key), params.get("corners"), params.get("auto", False))
        self.invalidate(p, "correct-geometry")
        p.corrected_image = self.put_image(self.key(p, "working/corrected.png"), image)
        p.geometry = meta
        self.record_cache(p, "correct-geometry", signature, [p.corrected_image])
        return {"geometry": meta}

    def _create_part(self, p: Project, mask: np.ndarray, fields: dict | None = None) -> Part:
        image = self.image(p.corrected_image)
        crop, bbox, local_mask = segmentation.crop_mask(image, mask)
        identifier = "part_" + uuid4().hex[:16]
        prefix = f"parts/{identifier}/"
        corrected = self.put_image(self.key(p, prefix + "corrected_crop.png"), crop)
        mask_key = self.put_image(self.key(p, prefix + "mask.png"), local_mask)
        # Preserve the exact pre-mask corrected crop as a source reference.
        x, y, w, h = bbox
        source = self.put_image(self.key(p, prefix + "source_crop.png"), image.crop((x, y, x + w, y + h)))
        return Part(part_id=identifier, bbox=bbox, mask=mask_key, source_crop=source,
                    corrected_crop=corrected, **(fields or {}))

    def stage_segment(self, p, params, cancelled):
        self.require(bool(p.corrected_image), "Correct geometry (or explicitly keep identity) first")
        if params.get("points") and not self.segmentation_provider:
            raise EngineError("NOT_IMPLEMENTED", "Click-assisted segmentation requires an injected segmentation provider; use manual polygons")
        signature = digest([self.file_hash(p.corrected_image), p.settings.segment_min_area_ratio, params])
        if self._cached(p, "segment", signature):
            return {"cached": True, "detected_parts": len(p.parts)}
        image = self.image(p.corrected_image)
        manual = params.get("parts")
        if manual:
            masks = [segmentation.manual_mask(image.size, f["polygon"]) for f in manual]
            meta = {"method": "manual_polygons", "warnings": [], "confidence": None}
        else:
            masks = None
            if self.segmentation_provider:
                try:
                    masks, meta = self.segmentation_provider.segment(image, params.get("points"))
                    if not masks:
                        raise ValueError("No masks")
                except Exception:
                    p.warnings.append("SEGMENTATION_FAILED: provider failed; OpenCV fallback used.")
            if masks is None:
                masks, meta = segmentation.detect_masks(image, p.settings.segment_min_area_ratio)
        new_parts = []
        for i, mask in enumerate(masks):
            if cancelled():
                raise EngineError("JOB_CANCELLED", "Segmentation cancelled")
            if np.asarray(mask).shape != (image.height, image.width):
                raise EngineError("SEGMENTATION_FAILED", "Provider mask dimensions do not match image")
            fields = {"name": f"Unknown part {i + 1}"}
            if manual:
                fields.update({k: v for k, v in manual[i].items() if k in {"type", "name", "polygon", "confirmed"}})
            new_parts.append(self._create_part(p, np.asarray(mask, np.uint8), fields))
        self.invalidate(p, "segment")
        p.parts = new_parts
        p.warnings.extend(meta.get("warnings", []))
        if manual:
            p.manual_changes.append({"action": "segment", "parts": manual, "timestamp": now()})
        artifacts = [key for part in p.parts for key in (part.mask, part.source_crop, part.corrected_crop)]
        self.record_cache(p, "segment", signature, artifacts)
        return {"detected_parts": len(p.parts), "segmentation": meta}

    def selected(self, p, params) -> list[Part]:
        self.require(bool(p.parts), "Segment artwork first")
        part_id = params.get("part_id")
        part_ids = params.get("part_ids")
        if part_id and part_ids:
            raise EngineError("INVALID_PART_SELECTION", "Use part_id or part_ids, not both", status=422)
        if part_id:
            part_ids = [part_id]
        if part_ids:
            ordered_ids = list(dict.fromkeys(part_ids))
            by_id = {part.part_id: part for part in p.parts}
            missing = [item for item in ordered_ids if item not in by_id]
            if missing:
                raise EngineError(
                    "PART_NOT_FOUND",
                    "Part selection contains an unknown component",
                    status=404,
                    diagnostics={"part_ids": missing},
                )
            return [by_id[item] for item in ordered_ids]
        return p.parts

    def stage_reconstruct(self, p, params, cancelled):
        if not p.settings.preserve_original_colors:
            raise EngineError("NOT_IMPLEMENTED", "Automatic color restoration is not implemented; use preserved colors or an injected reconstruction provider")
        if p.settings.ocr and p.settings.text_mode == "editable":
            raise EngineError("NOT_IMPLEMENTED", "Editable text replacement needs font matching and removal masks; outlined text tracing is supported")
        results = {}
        for part in self.selected(p, params):
            if cancelled():
                raise EngineError("JOB_CANCELLED", "Reconstruction cancelled")
            production_settings = p.settings.model_copy(
                update={
                    "max_colors": max(32, p.settings.max_colors),
                    "delta_e": min(2.0, p.settings.delta_e),
                    "min_region_area": min(2.0, p.settings.min_region_area),
                    "ocr": True,
                }
            )
            signature = digest([
                PRODUCTION_VECTOR_VERSION,
                colors.PALETTE_VERSION,
                self.file_hash(part.corrected_crop),
                production_settings.max_colors,
                production_settings.delta_e,
                production_settings.noise_reduction,
                production_settings.ocr,
            ])
            if part.cache.get("reconstruct") == signature and part.clean_reference and part.vectorization_source:
                results[part.part_id] = {"cached": True}
                continue
            self.invalidate(p, "reconstruct", {part.part_id})
            source = self.image(part.corrected_crop)
            if self.reconstruction_provider:
                p.usage["ai_calls"] += 1
                try:
                    restored = self.reconstruction_provider.reconstruct_region(source, {"part_id": part.part_id})
                    if restored.size != source.size:
                        raise ValueError("Provider changed dimensions")
                    source = restored
                except Exception:
                    part.warnings.append("AI_PROVIDER_UNAVAILABLE: reconstruction failed; deterministic source retained.")
            ref, trace, palette, meta = reconstruction.reconstruct(source, production_settings)
            prefix = f"parts/{part.part_id}/"
            part.clean_reference = self.put_image(self.key(p, prefix + "clean_reference.png"), ref)
            part.vectorization_source = self.put_image(self.key(p, prefix + "vectorization_source.png"), trace)
            part.palette = palette
            part.decomposition = decomposition.decompose(ref, palette)
            if production_settings.ocr:
                try:
                    if not self.ocr_provider:
                        raise EngineError("OCR_FAILED", "No OCR provider is available")
                    part.ocr_results = self.ocr_provider.detect(ref)
                except Exception:
                    part.warnings.append("OCR_FAILED: OCR unavailable or failed; artwork is still traced as vector geometry.")
            part.cache["reconstruct"] = signature
            results[part.part_id] = meta
        p.palette = [color for part in p.parts for color in part.palette]
        return {"parts": results, "cached": all(r.get("cached") for r in results.values())}

    def stage_vectorize(self, p, params, cancelled):
        results = {}
        for part in self.selected(p, params):
            self.require(bool(part.clean_reference and part.vectorization_source), "Reconstruct selected parts first")
            if cancelled():
                raise EngineError("JOB_CANCELLED", "Vectorization cancelled")
            fallback_trace = bool(params.get("fallback_trace", False))
            production_preset = {
                "FAST": "BALANCED",
                "BALANCED": "PRECISION",
                "PRECISION": "ULTRA",
                "ULTRA": "ULTRA",
            }[p.settings.preset]
            trace_settings = p.settings.model_copy(
                update={
                    "preset": "ULTRA" if fallback_trace else production_preset,
                    "vector_mode": "precision" if fallback_trace else p.settings.vector_mode,
                    "gradients": False if fallback_trace else p.settings.gradients,
                    "allow_contour_fallback": fallback_trace,
                    "max_trace_dimension": None,
                }
            )
            signature = digest([
                PRODUCTION_VECTOR_VERSION,
                self.file_hash(part.clean_reference),
                self.file_hash(part.vectorization_source),
                trace_settings.model_dump(),
                fallback_trace,
            ])
            if part.cache.get("vectorize") == signature and part.vector and self.storage.exists(part.vector):
                results[part.part_id] = {"cached": True}
                continue
            root, meta = vectorization.vectorize(
                self.image(part.clean_reference),
                self.image(part.vectorization_source),
                trace_settings,
                self.settings.tool_timeout_seconds,
            )
            data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            check = validate_svg(data, max_bytes=self.settings.max_svg_bytes, max_pixels=self.settings.max_pixels)
            if not check["true_vector"]:
                raise EngineError("VECTOR_TRACE_FAILED", "Trace failed integrity checks: " + "; ".join(check["errors"][:3]),diagnostics={'validation_errors':check['errors'],'trace_engine':meta.get('backend'),'fallback_attempted':meta.get('fallback_attempted',False)})
            if check["path_count"] > self.settings.max_paths:
                raise EngineError("VECTOR_COMPLEXITY_LIMIT", "Trace exceeds configured path limit; simplify settings or explicitly increase the limit")
            self.invalidate(p, "vectorize", {part.part_id})
            raw_key = self.key(p, f"vectors/{part.part_id}.raw.svg")
            self.storage.put(raw_key, data)
            part.vector = raw_key
            part.metrics = {
                "before_paths": check["path_count"],
                "before_anchors": check["total_anchor_count"],
                "embedded_rasters": check["embedded_rasters"],
                "backend": meta["backend"],
                "trace_metadata": meta,
                "production_vector_version": PRODUCTION_VECTOR_VERSION,
                "production_preset": trace_settings.preset,
                "explicit_fallback_trace": fallback_trace,
            }
            log_event(
                "vector_trace_complete",
                project_id=p.project_id,
                part_id=part.part_id,
                backend=meta.get("backend"),
                paths=check["path_count"],
                anchors=check["total_anchor_count"],
                fallback_attempted=meta.get("fallback_attempted", False),
                preset=trace_settings.preset,
            )
            if check["path_count"] > 10000:
                part.warnings.append(f"Complex texture generated {check['path_count']} paths; consider a lower detail preset.")
            part.warnings.extend(meta.get("warnings", []))
            part.cache["vectorize"] = signature
            results[part.part_id] = {**meta, "metrics": part.metrics}
        return {"parts": results, "cached": all(r.get("cached") for r in results.values())}

    def stage_optimize(self, p, params, cancelled):
        results = {}
        for part in self.selected(p, params):
            self.require(bool(part.vector), "Vectorize selected parts first")
            if cancelled():
                raise EngineError("JOB_CANCELLED", "Optimization cancelled")
            raw = self.key(p, f"vectors/{part.part_id}.raw.svg")
            optimize_preset = part.metrics.get("production_preset", p.settings.preset)
            signature = digest([PRODUCTION_VECTOR_VERSION, self.file_hash(raw), optimize_preset])
            if part.cache.get("optimize") == signature and part.vector and self.storage.exists(part.vector):
                results[part.part_id] = {"cached": True}
                continue
            root = SafeET.fromstring(self.storage.get(raw))
            metrics = optimize(root, optimize_preset)
            existing_ids = {element.get("id") for element in root.iter() if element.get("id")}
            for index, element in enumerate(root.iter()):
                if element.tag.split("}")[-1] in {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"} and not element.get("id"):
                    shape_id = 'shape_' + digest([element.tag,element.attrib])[:16]
                    while shape_id in existing_ids:
                        shape_id += "_"
                    element.set("id", shape_id)
                    existing_ids.add(shape_id)
            data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            check = validate_svg(data)
            if not check["true_vector"]:
                raise EngineError("SVG_VALIDATION_FAILED", "Optimized vector did not pass integrity checks")
            self.invalidate(p, "optimize", {part.part_id})
            part.vector = self.key(p, f"vectors/{part.part_id}.optimized.svg")
            self.storage.put(part.vector, data)
            part.metrics.update(metrics)
            part.metrics.update({"paths": check["path_count"], "anchors": check["total_anchor_count"],
                                 "rasters": check["embedded_rasters"], "status": "PASS"})
            part.cache["optimize"] = signature
            results[part.part_id] = metrics
        return {"parts": results, "cached": all(r.get("cached") for r in results.values())}

    def stage_compose(self, p, params, cancelled):
        selected = self.selected(p, params)
        selected_ids = [part.part_id for part in selected]
        self.require(
            bool(selected)
            and all(part.vector and part.cache.get("optimize") for part in selected),
            "Optimize every selected part before composing",
        )
        signature = digest(
            [
                [
                    part.model_dump(
                        exclude={
                            "cache",
                            "warnings",
                            "metrics",
                            "validation",
                            "previews",
                            "processing_state",
                            "error",
                        }
                    ),
                    self.file_hash(part.vector),
                ]
                for part in selected
            ]
            + [
                selected_ids,
                p.settings.known_width_mm,
                p.settings.bleed_mm,
                p.settings.safe_zone_mm,
            ]
        )
        if self._cached(p, "compose", signature):
            return {"cached": True, "selected_part_ids": selected_ids}

        self.invalidate(p, "compose")
        vectors = [
            (
                part,
                SafeET.fromstring(self.storage.get(part.vector)),
                np.asarray(self.image(part.mask).getchannel("R")),
            )
            for part in selected
        ]
        data = compose(p, vectors, tuple(p.geometry["output_dimensions"]))
        p.master_svg = self.key(p, "vectors/master.svg")
        self.storage.put(p.master_svg, data)
        part_outputs = []
        for part, root, mask in vectors:
            key = self.key(p, f"vectors/{part.part_id}.svg")
            self.storage.put(
                key,
                compose(
                    p,
                    [(part, root, mask)],
                    part.bbox[2:],
                    single_part=True,
                ),
            )
            part_outputs.append(key)
        self.record_cache(p, "compose", signature, [p.master_svg, *part_outputs])
        return {
            "master_svg": p.master_svg,
            "part_files": part_outputs,
            "selected_part_ids": selected_ids,
            "dimensions": (
                "calibrated" if p.settings.known_width_mm else "part-calibrated"
            ),
        }

    def stage_validate(self, p, params, cancelled):
        selected = self.selected(p, params)
        selected_ids = [part.part_id for part in selected]
        self.require(bool(p.master_svg), "Compose the selected SVG set first")
        composed_ids = (
            p.stage_metadata.get("compose", {}).get("selected_part_ids") or selected_ids
        )
        if set(composed_ids) != set(selected_ids):
            raise EngineError(
                "STALE_VALIDATION",
                "The selected part set changed after composition; compose again",
                status=409,
            )

        data = self.storage.get(p.master_svg)
        report = validate_svg(
            data,
            max_bytes=self.settings.max_svg_bytes,
            max_pixels=self.settings.max_pixels,
        )
        p.validation = report
        report["selected_part_ids"] = selected_ids
        report["validated_sha256"] = hashlib.sha256(data).hexdigest()
        report["resolution_independent"] = True
        report["embedded_raster_policy"] = "forbidden" if p.settings.export_mode == "true_vector" else "hybrid"
        if not report["valid_svg"]:
            raise EngineError("SVG_VALIDATION_FAILED", "; ".join(report["errors"][:5]))
        if report["embedded_rasters"] and p.settings.export_mode == "true_vector":
            raise EngineError("RASTER_FOUND_IN_TRUE_VECTOR", "Raster detected in True Vector project")
        if not report["meaningful_shape_count"] or report["illustrator_compatibility"] == "FAIL":
            raise EngineError("SVG_VALIDATION_FAILED", "No meaningful vector artwork or incompatible SVG")

        size = tuple(p.geometry["output_dimensions"])
        vector = render_svg(data, *size)
        reference = Image.new("RGBA", size)
        for part in selected:
            reference.alpha_composite(self.image(part.clean_reference), part.bbox[:2])
        metrics, difference = compare(reference, vector)
        report["render_succeeded"] = True
        report["visual_comparison"] = metrics
        if metrics["ssim"] is not None and metrics["ssim"] < .85:
            report["warnings"].append(
                "Visual similarity below 0.85 SSIM; review the clean reference, vector and difference previews before production."
            )
            report["illustrator_compatibility"] = "PASS_WITH_WARNINGS"

        report["parts"] = []
        for part in selected:
            part_data = self.storage.get(self.key(p, f"vectors/{part.part_id}.svg"))
            part_report = validate_svg(part_data)
            if not part_report["true_vector"]:
                raise EngineError(
                    "SVG_VALIDATION_FAILED",
                    "A selected composed part did not pass True Vector validation",
                    diagnostics={"part_id": part.part_id},
                )
            part_render = render_svg(part_data, part.bbox[2], part.bbox[3])
            part_diff, _ = compare(self.image(part.clean_reference), part_render)
            trace_backend = part.metrics.get("backend")
            production_quality = (
                "PASS"
                if trace_backend == "vtracer"
                and not part.metrics.get("explicit_fallback_trace")
                else "REVIEW_REQUIRED"
            )
            part.validation = {
                **part_report,
                "render_succeeded": True,
                "validated_sha256": hashlib.sha256(part_data).hexdigest(),
                "trace_backend": trace_backend,
                "production_quality": production_quality,
                "pathfinder_hierarchy": part.metrics.get("trace_metadata", {}).get("hierarchy"),
                "ocr_objects": len(part.ocr_results),
                "resolution_independent": True,
                "physical_width_mm": part.physical_width_mm,
                "physical_height_mm": part.physical_height_mm,
            }
            if production_quality != "PASS":
                part.validation["warnings"] = list(part.validation.get("warnings", [])) + [
                    "Production used an explicit/fallback contour trace; review paths before manufacturing."
                ]
            diagnostic_data, diagnostic_nodes = diagnostic(part_data)
            diagnostic_key = self.key(p, f"previews/{part.part_id}-paths.svg")
            self.storage.put(diagnostic_key, diagnostic_data)
            part.previews["vector_view"] = diagnostic_key
            part.metrics["visual_difference"] = part_diff
            report["parts"].append(
                {
                    "part_id": part.part_id,
                    "part": part.type,
                    "paths": part_report["path_count"],
                    "anchors": part_report["total_anchor_count"],
                    "rasters": part_report["embedded_rasters"],
                    "status": "PASS",
                    "validated_sha256": hashlib.sha256(part_data).hexdigest(),
                    "physical_width_mm": part.physical_width_mm,
                    "physical_height_mm": part.physical_height_mm,
                    "trace_backend": part.validation.get("trace_backend"),
                    "production_quality": part.validation.get("production_quality"),
                    "ocr_objects": part.validation.get("ocr_objects", 0),
                    "resolution_independent": True,
                }
            )

        p.previews = {
            "reference": self.put_image(self.key(p, "previews/reference.png"), reference),
            "vector_render": self.put_image(self.key(p, "previews/vector_render.png"), vector),
            "difference": self.put_image(self.key(p, "previews/difference.png"), difference),
        }
        view_data, nodes = diagnostic(data)
        view_key = self.key(p, "previews/vector_view.svg")
        self.storage.put(view_key, view_data)
        p.previews["vector_view"] = view_key
        nodes_key = self.key(p, "reports/nodes.json")
        self.storage.json(nodes_key, nodes)
        p.previews["nodes"] = nodes_key
        report.update(
            status="PASS",
            vector_status=report["status"],
            vector_paths=report["path_count"],
            editable_objects=report["shape_count"],
            geometry_integrity="PASS",
        )
        report["file_size_bytes"] = len(data)
        p.state = State.VALIDATED
        self.storage.save(p)
        self.storage.json(self.key(p, "reports/validation.json"), report)
        p.usage["output_paths"] = report["path_count"]
        p.usage["output_anchors"] = report["total_anchor_count"]
        return {"validation": report, "selected_part_ids": selected_ids}

    def stage_export(self, p, params, cancelled):
        self.require(p.state == State.READY and bool(p.validation and p.validation.get("render_succeeded")),
                     "Validate and render the current master before export")
        formats = params.get("formats", ["svg"])
        if not formats or any(f not in {"svg", "pdf", "eps", "png", "zip"} for f in formats):
            raise EngineError("UNSUPPORTED_EXPORT", "Supported formats: svg, pdf, eps, png, zip; native AI is not supported")
        data = self.storage.get(p.master_svg)
        if hashlib.sha256(data).hexdigest() != p.validation.get("validated_sha256"):
            raise EngineError("STALE_VALIDATION", "Master changed after validation; validate it again", status=409)
        exporter.approve(data, p.settings.export_mode)
        for part in p.parts:
            key = self.key(p, f"vectors/{part.part_id}.svg")
            record = next((entry for entry in p.validation.get("parts", []) if entry["part_id"] == part.part_id), {})
            if not self.storage.exists(key) or self.file_hash(key) != record.get("validated_sha256"):
                raise EngineError("STALE_VALIDATION", "Part changed after validation; validate again", status=409)
        failures = {}
        selected_ids = params.get("part_ids")
        if params.get("part_id"):
            if selected_ids:
                raise EngineError("INVALID_EXPORT_SELECTION", "Use part_id or part_ids, not both")
            selected_ids = [params["part_id"]]
        selected_ids = selected_ids or [part.part_id for part in p.parts]
        chosen = [part for part in p.parts if part.part_id in selected_ids]
        if selected_ids and (not chosen or len(set(selected_ids)) != len(chosen)):
            raise EngineError("PART_NOT_FOUND", "Export selection contains an unknown part", status=404)
        if chosen:
            p.stage_metadata["export_policy"]="parts_only_v1"
            part_files = {}
            for part in chosen:
                part_data = self.storage.get(self.key(p, f"vectors/{part.part_id}.svg"))
                validation = next((v for v in p.validation.get("parts", []) if v["part_id"] == part.part_id), {})
                if hashlib.sha256(part_data).hexdigest() != validation.get("validated_sha256"):
                    raise EngineError("STALE_VALIDATION", "Part changed after validation; validate again", status=409)
                exporter.approve(part_data, p.settings.export_mode)
                for format in [f for f in formats if f != "zip"]:
                    if cancelled():
                        raise EngineError("JOB_CANCELLED", "Part export cancelled")
                    key = self.key(p, f"exports/{part.part_id}.{format}")
                    try:
                        output = (
                            part_data
                            if format == "svg"
                            else exporter.rasterize_png(
                                part_data,
                                dpi=300,
                                timeout=self.settings.tool_timeout_seconds,
                                mode=p.settings.export_mode,
                                max_pixels=self.settings.max_export_pixels,
                            )
                            if format == "png"
                            else exporter.convert(
                                part_data,
                                format,
                                self.settings.tool_timeout_seconds,
                                p.settings.export_mode,
                            )
                        )
                        self.storage.put(key, output)
                        part.exports[format] = key
                        p.usage["export_operations"] += 1
                    except EngineError as exc:
                        failures[f"{part.part_id}:{format}"] = exc.as_dict()
                part_files[part.part_id] = dict(part.exports)
            exports = {}
            if "zip" in formats:
                token = digest([sorted(set(selected_ids)), sorted(formats), params.get("bundle", "selected_files")])[:16]
                bundle = params.get("bundle", "selected_files")
                if bundle == "production_pack":
                    key = self.key(p, f"exports/production-pack-{token}.zip")
                    payload = self.pack(p, selected_ids=set(selected_ids))
                    p.exports[f"production_pack_{token}"] = key
                else:
                    key = self.key(p, f"exports/selected-files-{token}.zip")
                    payload = self.pack_selected_files(p, set(selected_ids), set(formats))
                    p.exports[f"selected_files_{token}"] = key
                self.storage.put(key, payload)
                exports["zip"] = key
                p.usage["export_operations"] += 1
            return {
                "exports": exports,
                "part_files": part_files,
                "export_errors": failures,
                "success": not failures,
                "bundle": params.get("bundle", "selected_files"),
            }
        raise EngineError('INVALID_EXPORT_SELECTION','No real production parts selected')

    def pack_selected_files(self, p: Project, selected_ids: set[str], formats: set[str]) -> bytes:
        """Create the lightweight Download Selected Parts archive.

        This archive intentionally contains only the user-selected production
        files. Metadata, previews and handoff reports belong to Production Pack.
        """
        stream = BytesIO()
        requested = {fmt for fmt in formats if fmt != "zip"}
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for part in p.parts:
                if part.part_id not in selected_ids:
                    continue
                for fmt in sorted(requested):
                    key = part.exports.get(fmt)
                    if key and self.storage.exists(key):
                        archive.writestr(
                            f"{part.type}-{part.part_id}.{fmt}",
                            self.storage.get(key),
                        )
        return stream.getvalue()

    def pack(self, p: Project, selected_ids: set[str] | None = None) -> bytes:
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for part in p.parts:
                if selected_ids is not None and part.part_id not in selected_ids:
                    continue
                key = self.key(p, f"vectors/{part.part_id}.svg")
                if self.storage.exists(key):
                    archive.writestr(f"parts/{part.type}-{part.part_id}.svg", self.storage.get(key))
                for format, export_key in part.exports.items():
                    if format != "svg" and self.storage.exists(export_key):
                        archive.writestr(f"parts/{part.type}-{part.part_id}.{format}", self.storage.get(export_key))
            for part in p.parts:
                if selected_ids is not None and part.part_id not in selected_ids:
                    continue
                if part.clean_reference:
                    archive.writestr(f"previews/{part.part_id}-reference.png", self.storage.get(part.clean_reference))
                key = self.key(p, f"vectors/{part.part_id}.svg")
                archive.writestr(f"previews/{part.part_id}-vector.png", ingest.png_bytes(render_svg(self.storage.get(key),part.bbox[2],part.bbox[3])))
            public_metadata=p.model_dump(exclude={'assistant_sessions','master_svg','exports','previews'})
            if selected_ids:
                public_metadata['parts']=[a for a in public_metadata['parts'] if a['part_id'] in selected_ids]
            archive.writestr("metadata/project.json", json.dumps(public_metadata,indent=2))
            archive.writestr("metadata/validation.json", json.dumps(p.validation, indent=2))
            archive.writestr("metadata/palette.json", json.dumps(p.palette, indent=2))
            archive.writestr("metadata/export-selection.json", json.dumps({"part_ids": sorted(selected_ids) if selected_ids else [part.part_id for part in p.parts],
                                                                           "includes_master": False}))
            archive.writestr("README.txt", "ReVector production pack\nSVG contains editable geometry and no embedded raster.\nDimensions are " +
                             ("calibrated." if p.settings.known_width_mm else "uncalibrated; supply known_width_mm before physical production.") +
                             "\nIndividual part files use supplied dimensions when present. No assembled pattern is included.\n"
                             "Review difference preview and warnings. Static Illustrator compatibility is not an Adobe application test.\n")
        return stream.getvalue()

    def manual(self, project_id: str, action: str, part_id: str | None, changes: dict) -> Project:
        with self.storage.lock(project_id):
            p = self.load(project_id)
            self.require(bool(p.corrected_image), "Correct geometry before modifying parts")
            image = self.image(p.corrected_image)
            matches = [part for part in p.parts if part.part_id == part_id]
            part = matches[0] if matches else None
            if action != "add" and not part:
                raise EngineError("PART_NOT_FOUND", "Part does not exist", status=404)
            if part and part.locked and changes != {"locked": False}:
                raise EngineError("PART_LOCKED", "Unlock the part before editing", status=409)
            if action == "add":
                mask = segmentation.manual_mask(image.size, changes["polygon"])
                p.parts.append(self._create_part(p, mask, {"source":"manual",**{k: v for k, v in changes.items() if k in {"type", "name", "polygon", "confirmed"}}}))
                self.invalidate(p, "compose")
            elif action == "remove":
                p.parts = [item for item in p.parts if item.part_id != part_id]
                self.invalidate(p, "compose")
            elif action in {"update", "confirm"}:
                update_fields = ("name", "type", "locked", "confirmed", "physical_width_mm", "physical_height_mm", "bleed_mm", "safe_zone_mm")
                try:
                    Part.model_validate({**part.model_dump(), **{k: v for k, v in changes.items() if k in update_fields}})
                except ValueError as exc:
                    raise EngineError("INVALID_PART_SETTINGS", "Supply valid part dimensions together; physical offsets require dimensions", status=422) from exc
                if "polygon" in changes:
                    mask = segmentation.manual_mask(image.size, changes["polygon"])
                    crop, bbox, local_mask = segmentation.crop_mask(image, mask)
                    self.invalidate(p, "reconstruct", {part.part_id})
                    part.source = "manual"
                    part.bbox, part.polygon = bbox, [tuple(point) for point in changes["polygon"]]
                    self.put_image(part.mask, local_mask)
                    self.put_image(part.corrected_crop, crop)
                    x, y, w, h = bbox
                    self.put_image(part.source_crop, image.crop((x, y, x + w, y + h)))
                else:
                    self.invalidate(p, "compose")
                for field in update_fields:
                    if field in changes:
                        setattr(part, field, changes[field])
                if action == "confirm":
                    part.confirmed = True
                # Validate assignments made above before persistence.
                Part.model_validate(part.model_dump())
            elif action in {"merge", "split"}:
                if action == "split":
                    polygons = changes.get("polygons", [])
                    if len(polygons) < 2:
                        raise EngineError("INVALID_BOUNDARY", "Split needs at least two explicit polygons")
                    masks = [segmentation.manual_mask(image.size, polygon) for polygon in polygons]
                    ids = {part.part_id}
                else:
                    ids = set(changes.get("part_ids", [])) | {part.part_id}
                    chosen = [item for item in p.parts if item.part_id in ids]
                    if len(chosen) < 2 or len(chosen) != len(ids) or any(item.locked for item in chosen):
                        raise EngineError("INVALID_MERGE", "Select at least two existing unlocked parts")
                    merged = np.zeros((image.height, image.width), np.uint8)
                    for item in chosen:
                        x, y, w, h = item.bbox
                        merged[y:y + h, x:x + w] |= np.asarray(self.image(item.mask).getchannel("R"))
                    masks = [merged]
                new = [self._create_part(p, mask, {"name": changes.get("name", "Manual part"), "type": changes.get("type", "unknown")}) for mask in masks]
                p.parts = [item for item in p.parts if item.part_id not in ids] + new
                self.invalidate(p, "compose")
            else:
                raise EngineError("NOT_IMPLEMENTED", "Unknown manual correction action")
            from app.ai.contracts import SLOTS
            for slot in p.slots.values():
                matches=[item for item in p.parts if item.type==slot.part_type.lower()]
                if len(matches)==1:
                    slot.part_id=matches[0].part_id
                    slot.status='confirmed' if matches[0].confirmed else 'manual'
                elif len(matches)>1:
                    slot.status='uncertain'
                elif slot.status!='blank':
                    slot.part_id=None;slot.status='missing'
            p.ai_metadata.pop('review',None)
            p.cache.pop("segment", None)
            p.manual_changes.append({"action": action, "part_id": part_id, "changes": changes, "timestamp": now()})
            if not p.parts or any(not item.clean_reference for item in p.parts):
                p.state = State.SEGMENTED
            elif any(not item.vector for item in p.parts):
                p.state = State.RECONSTRUCTED
            elif any(not item.cache.get("optimize") for item in p.parts):
                p.state = State.VECTORIZED
            else:
                p.state = State.OPTIMIZED
            self.storage.save(p)
            return p

    def edit_fill(self, project_id: str, part_id: str, shape_id: str, fill: str) -> Project:
        """Edit one validated solid-fill object, then require fresh composition and validation."""
        import re
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", fill):
            raise EngineError("INVALID_VECTOR_EDIT", "Fill must be a six-digit hex color", status=422)
        with self.storage.lock(project_id):
            p = self.load(project_id)
            part = next((part for part in p.parts if part.part_id == part_id), None)
            if not part or not part.vector or not part.cache.get("optimize"):
                raise EngineError("PART_NOT_FOUND", "An optimized part is required", status=404)
            if part.locked:
                raise EngineError("PART_LOCKED", "Unlock this part before changing its artwork", status=409)
            root = SafeET.fromstring(self.storage.get(part.vector))
            local_id = shape_id.removeprefix(part.part_id + "_")
            shape = next((element for element in root.iter() if element.get("id") == local_id), None)
            if shape is None or shape.tag.split("}")[-1] not in {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}:
                raise EngineError("VECTOR_SHAPE_NOT_FOUND", "Select a real artwork shape", status=404)
            shape.set("fill", fill.lower())
            # Inline fill declarations override attributes, so remove only that declaration.
            style = shape.get("style")
            if style:
                shape.set("style", ";".join(declaration for declaration in style.split(";") if declaration.split(":")[0].strip().lower() != "fill"))
            data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            exporter.approve(data)
            self.storage.put(part.vector, data)
            self.invalidate(p, "compose")
            p.state = State.OPTIMIZED
            p.manual_changes.append({"action": "vector_fill", "part_id": part_id, "shape_id": local_id, "fill": fill, "timestamp": now()})
            self.storage.save(p)
            return p

    def delete(self, project_id: str):
        with self.storage.lock(project_id):
            self.load(project_id)
            self.storage.delete_prefix(f"projects/{project_id}")
