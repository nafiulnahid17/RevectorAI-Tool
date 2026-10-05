"""Persisted project and processing contracts."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator, computed_field
from app import __version__
from app.ai.contracts import PartSlot, SLOTS


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class State(StrEnum):
    PART_REVIEW_READY = "PART_REVIEW_READY"
    CREATED = "CREATED"
    UPLOADED = "UPLOADED"
    ANALYZING = "ANALYZING"
    ANALYZED = "ANALYZED"
    GEOMETRY_CORRECTED = "GEOMETRY_CORRECTED"
    SEGMENTING = "SEGMENTING"
    SEGMENTED = "SEGMENTED"
    RECONSTRUCTING = "RECONSTRUCTING"
    RECONSTRUCTED = "RECONSTRUCTED"
    VECTORIZING = "VECTORIZING"
    VECTORIZED = "VECTORIZED"
    OPTIMIZING = "OPTIMIZING"
    OPTIMIZED = "OPTIMIZED"
    COMPOSING = "COMPOSING"
    COMPOSED = "COMPOSED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    READY = "READY"
    FAILED = "FAILED"


PartType = Literal["front_body", "back_body", "left_sleeve", "right_sleeve", "left_shoulder",
                   "right_shoulder", "front_collar", "back_collar", "left_cuff", "right_cuff",
                   "trim", "top_trim", "bottom_trim", "other_part", "unknown"]


class ProcessingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["FAST", "BALANCED", "PRECISION", "ULTRA"] = "BALANCED"
    vector_mode: Literal["precision", "color", "mono", "reconstruction"] = "color"
    max_colors: int = Field(12, ge=2, le=64)
    delta_e: float = Field(5, ge=0, le=30)
    min_region_area: float = Field(6, ge=0, le=10000)
    segment_min_area_ratio: float = Field(0.01, ge=0.0001, le=0.9)
    noise_reduction: bool = True
    preserve_original_colors: bool = True
    image_quality: Literal["LOW", "MEDIUM", "HIGH", "MAX"] = "MEDIUM"
    mockup_background: Literal["black", "white"] = "black"
    ocr: bool = False
    text_mode: Literal["outlined", "editable"] = "outlined"
    gradients: bool = True
    allow_contour_fallback: bool = True
    max_trace_dimension: int | None = Field(None, ge=64, le=12000)
    mockup_width: int = Field(1536, ge=1024, le=1920, multiple_of=16)
    mockup_height: int = Field(1024, ge=768, le=1920, multiple_of=16)
    ai_workflow: bool = True
    # No automatic raster embedding. Hybrid reconstruction still creates vectors.
    export_mode: Literal["true_vector", "hybrid"] = "true_vector"
    known_width_mm: float | None = Field(None, gt=0)
    bleed_mm: float = Field(0, ge=0, le=100)
    safe_zone_mm: float = Field(0, ge=0, le=100)

    @model_validator(mode="after")
    def physical_geometry(self):
        if (self.bleed_mm or self.safe_zone_mm) and self.known_width_mm is None:
            raise ValueError("Physical bleed/safe zone requires known_width_mm calibration")
        return self


class Part(BaseModel):
    part_id: str = Field(default_factory=lambda: "part_" + uuid4().hex[:16])
    type: PartType = "unknown"
    name: str = "Unknown part"
    bbox: tuple[int, int, int, int]
    mask: str
    source_crop: str
    corrected_crop: str
    polygon: list[tuple[float, float]] | None = None
    confirmed: bool = False
    locked: bool = False
    confidence: float | None = None
    ai_confidence: float | None = Field(None,ge=0,le=1)
    source: Literal["ai_detected","engine_refined","manual","ai_reconstructed"] = "engine_refined"
    processing_state: str = "DETECTED"
    error: dict | None = None
    validation: dict | None = None
    previews: dict[str,str] = Field(default_factory=dict)
    clean_reference: str | None = None
    vectorization_source: str | None = None
    palette: list[dict] = Field(default_factory=list)
    ocr_results: list[dict] = Field(default_factory=list)
    decomposition: dict = Field(default_factory=dict)
    vector: str | None = None
    metrics: dict = Field(default_factory=dict)
    cache: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    physical_width_mm: float | None = Field(None, gt=0, le=10000)
    physical_height_mm: float | None = Field(None, gt=0, le=10000)
    bleed_mm: float = Field(0, ge=0, le=100)
    safe_zone_mm: float = Field(0, ge=0, le=100)
    exports: dict[str, str] = Field(default_factory=dict)

    @computed_field
    @property
    def engine_bbox(self) -> tuple[int,int,int,int]:
        return self.bbox

    @computed_field
    @property
    def engine_polygon(self) -> list[tuple[float,float]] | None:
        return self.polygon

    @model_validator(mode="after")
    def part_dimensions(self):
        if (self.physical_width_mm is None) != (self.physical_height_mm is None):
            raise ValueError("Supply both part width and height, or neither")
        if (self.bleed_mm or self.safe_zone_mm) and self.physical_width_mm is None:
            raise ValueError("Part bleed/safe zone requires physical dimensions")
        return self


class PartRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    type: PartType = "unknown"
    width_mm: float = Field(gt=0, le=10000)
    height_mm: float = Field(gt=0, le=10000)


class Project(BaseModel):
    project_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field("Untitled", max_length=120)
    user_id: str | None = Field(None, max_length=128)
    request_id: str | None = Field(None, max_length=128)
    state: State = State.CREATED
    engine_version: str = __version__
    source_file: str | None = None
    source_hash: str | None = None
    source_metadata: dict = Field(default_factory=dict)
    working_image: str | None = None
    corrected_image: str | None = None
    thumbnail: str | None = None
    settings: ProcessingSettings = Field(default_factory=ProcessingSettings)
    analysis: dict = Field(default_factory=dict)
    geometry: dict = Field(default_factory=dict)
    parts: list[Part] = Field(default_factory=list)
    slots: dict[str, PartSlot] = Field(default_factory=lambda: {name:PartSlot(part_type=name) for name in SLOTS})
    ai_assets: dict[str,str] = Field(default_factory=dict)
    ai_metadata: dict = Field(default_factory=dict)
    events: list[dict] = Field(default_factory=list)
    error_history: list[dict] = Field(default_factory=list)
    assistant_sessions: dict = Field(default_factory=dict)
    production_specifications: list[PartRequirement] = Field(default_factory=list, max_length=100)
    manual_changes: list[dict] = Field(default_factory=list)
    palette: list[dict] = Field(default_factory=list)
    cache: dict = Field(default_factory=dict)
    stage_metadata: dict = Field(default_factory=dict)
    master_svg: str | None = None
    validation: dict | None = None
    previews: dict = Field(default_factory=dict)
    exports: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=lambda: {"processing_ms": 0, "ai_calls": 0, "export_operations": 0})
    warnings: list[str] = Field(default_factory=list)
    error: dict | None = None
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)

    @property
    def true_vector_ready(self) -> bool:
        v = self.validation or {}
        return bool(self.state == State.READY and v.get("valid_svg") and v.get("true_vector")
                    and v.get("embedded_rasters") == 0 and v.get("path_count", 0) > 0
                    and v.get("illustrator_compatibility") != "FAIL" and v.get("render_succeeded"))
