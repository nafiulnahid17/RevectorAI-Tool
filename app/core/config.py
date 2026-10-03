"""Environment-based settings. No AI credential is needed for deterministic operation."""
from pathlib import Path
import importlib.util
import shutil
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REVECTOR_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    max_upload_bytes: int = Field(50 * 1024 * 1024, ge=1024)
    max_pixels: int = Field(40_000_000, ge=100)
    worker_threads: int = Field(2, ge=1, le=8)
    sync_jobs: bool = False
    tool_timeout_seconds: int = Field(180, ge=1)
    max_svg_bytes: int = Field(32 * 1024 * 1024, ge=1024)
    max_paths: int = Field(100_000, ge=100)
    ocr_provider: str = "tesseract"
    segmentation_provider: str = "opencv"
    storage_backend: str = "local"
    api_key: SecretStr | None = None
    allow_unauthenticated: bool = False
    r2_endpoint: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None


def capabilities() -> dict[str, bool]:
    return {
        "opencv": importlib.util.find_spec("cv2") is not None,
        "vtracer": importlib.util.find_spec("vtracer") is not None,
        "potrace": shutil.which("potrace") is not None,
        "inkscape": shutil.which("inkscape") is not None,
        "resvg": importlib.util.find_spec("resvg_py") is not None or shutil.which("resvg") is not None,
        "tesseract": shutil.which("tesseract") is not None,
    }
