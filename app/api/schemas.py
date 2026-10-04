from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.models.project import PartType, ProcessingSettings, PartRequirement


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(StrictModel):
    name: str = Field("Untitled", max_length=120)
    user_id: str | None = Field(None, max_length=128)
    request_id: str | None = Field(None, max_length=128)
    settings: ProcessingSettings = Field(default_factory=ProcessingSettings)
    production_specifications: list[PartRequirement] = Field(default_factory=list, max_length=100)


class StageRequest(StrictModel):
    project_id: str
    part_id: str | None = None


class GeometryRequest(StageRequest):
    corners: list[tuple[float, float]] | None = Field(None, min_length=4, max_length=4)
    auto: bool = False


class ManualPart(StrictModel):
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=10000)
    type: PartType = "unknown"
    name: str = Field("Manual part", max_length=120)
    confirmed: bool = False


class SegmentRequest(StageRequest):
    parts: list[ManualPart] | None = Field(None, max_length=100)
    points: list[tuple[float, float]] | None = Field(None, max_length=1000)


class ExportRequest(StageRequest):
    formats: list[Literal["svg", "pdf", "eps", "png", "zip"]] = Field(default_factory=lambda: ["svg"], min_length=1, max_length=5)
    part_ids: list[str] | None = Field(None, min_length=1, max_length=100)


class SettingsUpdate(StrictModel):
    """Partial settings are validated against the stored project after merging."""
    ai_workflow: bool | None = None
    mockup_width: int | None = Field(None,ge=1024,le=1920,multiple_of=16)
    mockup_height: int | None = Field(None,ge=768,le=1920,multiple_of=16)
    preset: Literal["FAST", "BALANCED", "PRECISION", "ULTRA"] | None = None
    vector_mode: Literal["precision", "color", "mono", "reconstruction"] | None = None
    max_colors: int | None = Field(None, ge=2, le=64)
    delta_e: float | None = Field(None, ge=0, le=30)
    min_region_area: float | None = Field(None, ge=0, le=10000)
    segment_min_area_ratio: float | None = Field(None, ge=.0001, le=.9)
    noise_reduction: bool | None = None
    preserve_original_colors: bool | None = None
    ocr: bool | None = None
    text_mode: Literal["outlined", "editable"] | None = None
    gradients: bool | None = None
    allow_contour_fallback: bool | None = None
    max_trace_dimension: int | None = Field(None, ge=64, le=12000)
    export_mode: Literal["true_vector", "hybrid"] | None = None
    known_width_mm: float | None = Field(None, gt=0)
    bleed_mm: float | None = Field(None, ge=0, le=100)
    safe_zone_mm: float | None = Field(None, ge=0, le=100)


class PartUpdate(StrictModel):
    project_id: str
    polygon: list[tuple[float, float]] | None = Field(None, min_length=3, max_length=10000)
    name: str | None = Field(None, max_length=120)
    type: PartType | None = None
    locked: bool | None = None
    confirmed: bool | None = None
    physical_width_mm: float | None = Field(None, gt=0, le=10000)
    physical_height_mm: float | None = Field(None, gt=0, le=10000)
    bleed_mm: float | None = Field(None, ge=0, le=100)
    safe_zone_mm: float | None = Field(None, ge=0, le=100)


class PartAction(StrictModel):
    project_id: str
    action: Literal["add", "remove", "merge", "split"]
    part_id: str | None = None
    polygon: list[tuple[float, float]] | None = Field(None, min_length=3, max_length=10000)
    polygons: list[list[tuple[float, float]]] | None = Field(None, max_length=100)
    part_ids: list[str] | None = Field(None, max_length=100)
    name: str = Field("Manual part", max_length=120)
    type: PartType = "unknown"


class VectorEdit(StrictModel):
    project_id: str
    shape_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    fill: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
