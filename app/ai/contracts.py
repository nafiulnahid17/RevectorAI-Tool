"""Validated semantic evidence. AI candidates never become final masks directly."""

from typing import Literal, Protocol

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

SLOTS = (
    "LEFT_SLEEVE",
    "RIGHT_SLEEVE",
    "FRONT_BODY",
    "BACK_BODY",
    "FRONT_COLLAR",
    "BACK_COLLAR",
    "TOP_TRIM",
    "BOTTOM_TRIM",
)
SlotType = Literal[
    "LEFT_SLEEVE",
    "RIGHT_SLEEVE",
    "FRONT_BODY",
    "BACK_BODY",
    "FRONT_COLLAR",
    "BACK_COLLAR",
    "TOP_TRIM",
    "BOTTOM_TRIM",
]


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    part_type: SlotType
    candidate_bbox: tuple[float, float, float, float] = Field(
        description="Normalized x,y,width,height in [0,1]"
    )
    confidence: float | None = Field(None, ge=0, le=1)
    uncertain: bool = False
    notes: str = Field("", max_length=2000)


class ArtworkAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    artwork_type: str = "unknown"
    expected_parts: list[SlotType] = Field(default_factory=lambda: list(SLOTS))
    visible_parts: list[SlotType] = Field(default_factory=list)
    missing_parts: list[SlotType] = Field(default_factory=list)
    uncertain_parts: list[SlotType] = Field(default_factory=list)
    dominant_colors: list[str] = Field(default_factory=list, max_length=64)
    logos: list[dict] = Field(default_factory=list, max_length=100)
    text_regions: list[dict] = Field(default_factory=list, max_length=100)
    names: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    sponsors: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    collar_design: str = ""
    sleeve_design: str = ""
    confidence: float | None = Field(None, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)


class MockupQC(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    pass_qc: bool = False
    serious_failure: bool = False
    component_count: int = Field(0, ge=0, le=32)
    missing_parts: list[SlotType] = Field(default_factory=list)
    duplicate_parts: list[SlotType] = Field(default_factory=list)
    extra_components: list[str] = Field(default_factory=list, max_length=32)
    attached_collar: bool = False
    attached_sleeve: bool = False
    left_right_mixup: bool = False
    logo_or_crest_drift: bool = False
    sponsor_or_text_drift: bool = False
    name_or_number_drift: bool = False
    color_drift: bool = False
    pattern_loss: bool = False
    invented_branding: bool = False
    notes: list[str] = Field(default_factory=list, max_length=64)


class PartSlot(BaseModel):
    part_type: SlotType
    status: Literal[
        "detected",
        "missing",
        "uncertain",
        "manual",
        "ai_reconstructed",
        "confirmed",
        "blank",
    ] = "missing"
    part_id: str | None = None
    ai_confidence: float | None = None
    candidate_bbox: list[float] | None = None
    notes: list[str] = Field(default_factory=list)


class AIProvider(Protocol):
    name: str

    def analyze_artwork(self, image: Image.Image) -> dict: ...
    def enhance_artwork(
        self, image: Image.Image, size: tuple[int, int]
    ) -> Image.Image: ...
    def create_pattern_mockup(
        self, image: Image.Image, prompt: str, size: tuple[int, int]
    ) -> Image.Image: ...
    def verify_pattern_mockup(self, image: Image.Image) -> dict: ...
    def identify_parts(self, image: Image.Image) -> list[dict]: ...
    def reconstruct_missing_part(
        self, image: Image.Image, prompt: str, size: tuple[int, int]
    ) -> Image.Image: ...
    def explain_error(self, context: dict) -> dict: ...
