from pathlib import Path
import pytest
from app.core.config import Settings
from app.core.engine import Engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def engine(tmp_path):
    return Engine(Settings(data_dir=tmp_path / "data", sync_jobs=True))


@pytest.fixture
def simple_bytes():
    return (FIXTURES / "simple_2_color.png").read_bytes()
