"""Real Tesseract OCR. Font identity is always approximate without a font matcher."""
import csv
from io import StringIO
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from PIL import Image
from app.core.exceptions import EngineError


class TesseractOCR:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def detect(self, image: Image.Image) -> list[dict]:
        executable = shutil.which("tesseract")
        if not executable:
            raise EngineError("OCR_FAILED", "Tesseract is not installed")
        with TemporaryDirectory(prefix="revector-ocr-") as temp:
            source = Path(temp) / "input.png"
            bg = Image.new("RGB", image.size, "white")
            bg.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
            bg.save(source)
            try:
                result = subprocess.run([executable, str(source), "stdout", "--psm", "11", "tsv"],
                                        capture_output=True, timeout=self.timeout, check=True)
            except (subprocess.SubprocessError, OSError) as exc:
                raise EngineError("OCR_FAILED", "Tesseract failed or timed out") from exc
        texts = []
        for row in csv.DictReader(StringIO(result.stdout.decode()), delimiter="\t"):
            if not row.get("text", "").strip() or float(row["conf"]) < 0:
                continue
            x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
            texts.append({"text": row["text"], "bbox": [x, y, w, h],
                          "model_confidence": float(row["conf"]) / 100,
                          "rotation": None, "estimated_font_style": None,
                          "estimated_color": None, "font_match_status": "approximate"})
        return texts
