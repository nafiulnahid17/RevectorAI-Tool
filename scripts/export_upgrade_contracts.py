"""Static schemas/catalog to integrate a separate UI without exposing credentials."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.upgrade import (
    AssistantRequest,
    FeedbackRequest,
    MissingRequest,
    RecoveryRequest,
    ReviewRequest,
)
from app.core.config import Settings
from app.errors.assistant import Advice
from app.errors.catalog import ACTION_ALLOWLIST, CATALOG
from app.main import create_app
from app.models.project import Project
from app.models.upgrade import ErrorEvent, JobResponse

folder = Path("docs/upgrade/contracts")
folder.mkdir(parents=True, exist_ok=True)
for cls in [
    Project,
    ErrorEvent,
    JobResponse,
    Advice,
    ReviewRequest,
    MissingRequest,
    RecoveryRequest,
    AssistantRequest,
    FeedbackRequest,
]:
    (folder / f"{cls.__name__}.schema.json").write_text(
        json.dumps(cls.model_json_schema(), indent=2)
    )
(folder / "offline-catalog.json").write_text(
    json.dumps(
        {
            "version": "1.0",
            "source": "deterministic_catalog",
            "categories": {
                name: {"title": entry[0], "explanation": entry[1], "actions": entry[2]}
                for name, entry in CATALOG.items()
            },
            "allowlisted_actions": sorted(ACTION_ALLOWLIST),
        },
        indent=2,
    )
)
(folder / "openapi.json").write_text(
    json.dumps(
        create_app(Settings(data_dir=Path("data"), api_key=None)).openapi(), indent=2
    )
)
print(
    "Exported backend schemas and deterministic offline catalog; no provider settings or credentials included."
)
