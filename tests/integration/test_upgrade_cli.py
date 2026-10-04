"""CLI remains useful while preserving the part-only production export policy."""

import subprocess
import sys
import zipfile
from tests.conftest import FIXTURES


def test_cli_run_emits_real_individual_parts_and_no_master(tmp_path):
    output = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "--data-dir",
            str(tmp_path / "data"),
            "run",
            str(FIXTURES / "simple_2_color.png"),
            "--strategy",
            "precision",
            "--formats",
            "svg",
            "zip",
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert len(list((output / "parts").glob("*.svg"))) == 2
    assert not list(output.rglob("*master*"))
    with zipfile.ZipFile(output / "parts-only-production.zip") as pack:
        assert not any(
            "master" in name or "vector_view.svg" in name for name in pack.namelist()
        )
