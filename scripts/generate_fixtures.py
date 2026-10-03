"""Generate imperfect as well as ideal reproducible CV regression inputs."""
from pathlib import Path
import json
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFilter


def generate(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    simple = Image.new("RGB", (240, 180), "white")
    draw = ImageDraw.Draw(simple)
    draw.rectangle((20, 20, 110, 160), fill="#502080")
    draw.ellipse((130, 45, 210, 135), fill="#502080")
    simple.save(root / "simple_2_color.png")
    xx = np.linspace(0, 1, 240)[None, :, None]
    a, b = np.array([45, 12, 90]), np.array([205, 150, 235])
    gradient = np.tile((a[None, None] * (1 - xx) + b[None, None] * xx).astype(np.uint8), (180, 1, 1))
    Image.fromarray(gradient).save(root / "gradient.png")
    rng = np.random.default_rng(27)
    splatter = Image.fromarray(gradient)
    draw = ImageDraw.Draw(splatter)
    for _ in range(130):
        x, y = rng.integers([0, 0], [240, 180]); radius = int(rng.integers(1, 7))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#f5deff")
    splatter.save(root / "splatter.png")
    text = Image.new("RGB", (240, 180), "white")
    ImageDraw.Draw(text).text((35, 55), "REVECTOR 23", fill="black", font_size=26)
    text.save(root / "text.png")
    logo = Image.new("RGB", (240, 180), "white")
    draw = ImageDraw.Draw(logo)
    draw.polygon([(30, 145), (115, 20), (200, 145), (120, 105)], fill="black")
    draw.ellipse((85, 65, 120, 100), fill="white")
    logo.save(root / "logo.png")
    simple.resize((30, 23)).save(root / "low_resolution.png")
    source = np.asarray(simple)
    matrix = cv2.getPerspectiveTransform(np.float32([[0, 0], [239, 0], [239, 179], [0, 179]]),
                                        np.float32([[20, 15], [225, 2], [235, 172], [1, 155]]))
    Image.fromarray(cv2.warpPerspective(source, matrix, (240, 180), borderValue=(180, 180, 180))).save(root / "perspective.png")
    lighting = np.linspace(.4, 1, 240)[None, :, None]
    shadowed = np.clip(source * lighting, 0, 255).astype(np.uint8)
    Image.fromarray(shadowed).filter(ImageFilter.GaussianBlur(.8)).save(root / "shadowed.jpg", quality=45)
    panel = splatter.convert("RGBA")
    alpha = Image.new("L", panel.size)
    ImageDraw.Draw(alpha).polygon([(15, 5), (225, 5), (220, 50), (198, 85), (208, 175),
                                   (32, 175), (42, 85), (20, 50)], fill=255)
    panel.putalpha(alpha)
    ImageDraw.Draw(panel).polygon([(30, 60), (65, 150), (40, 170)], fill="white")
    panel.save(root / "complex_panel.png")
    multiple = Image.new("RGBA", (520, 240), (0, 0, 0, 0))
    multiple.alpha_composite(panel, (10, 20))
    multiple.alpha_composite(panel.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (270, 20))
    multiple.save(root / "multiple_panels.png")
    (root / "expectations.json").write_text(json.dumps({
        "simple_2_color.png": {"min_parts": 2, "true_vector": True},
        "gradient.png": {"linear_gradient_fit": True, "true_vector": True},
        "splatter.png": {"true_vector": True, "approximation_expected": True},
        "text.png": {"true_vector": True, "no_exact_font_claim": True},
        "logo.png": {"true_vector": True, "holes_preserved": True},
        "low_resolution.png": {"true_vector": True, "lost_detail_unrecoverable": True},
        "perspective.png": {"manual_corners_supported": True, "true_vector": True},
        "shadowed.jpg": {"lighting_is_heuristic": True, "true_vector": True},
        "complex_panel.png": {"min_parts": 1, "identity": "unknown", "true_vector": True},
        "multiple_panels.png": {"min_parts": 2, "identity": "unknown", "true_vector": True},
    }, indent=2))


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "tests" / "fixtures")
