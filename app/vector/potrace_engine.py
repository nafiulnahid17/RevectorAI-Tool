import shutil
import subprocess
from pathlib import Path
from PIL import Image
import cv2
import numpy as np
from defusedxml import ElementTree as SafeET
from app.core.exceptions import EngineError
from app.models.project import ProcessingSettings
from app.vector.presets import PRESETS


def trace(image: Image.Image, folder: Path, settings: ProcessingSettings, timeout: int):
    executable = shutil.which("potrace")
    if not executable:
        raise EngineError("VECTOR_TRACE_FAILED", "Potrace executable is unavailable")
    rgba = np.asarray(image.convert("RGBA"))
    gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary[rgba[..., 3] < 8] = 255
    source, destination = folder / "mono.pbm", folder / "potrace.svg"
    Image.fromarray(binary).convert("1").save(source)
    try:
        subprocess.run([executable, str(source), "--svg", "--output", str(destination),
                        "--turdsize", str(PRESETS[settings.preset]["filter_speckle"]),
                        "--opttolerance", str(PRESETS[settings.preset]["epsilon"]), "--unit", "1"],
                       check=True, capture_output=True, timeout=timeout)
        return SafeET.fromstring(destination.read_bytes()), {"backend": "potrace"}
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise EngineError("VECTOR_TRACE_FAILED", "Potrace failed or timed out") from exc
