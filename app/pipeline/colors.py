"""Deterministic palette clustering and measured CIE76 merging."""
import cv2
import numpy as np
from PIL import Image


def quantize(image: Image.Image, max_colors: int, delta_e: float) -> tuple[Image.Image, list[dict], np.ndarray]:
    rgba = np.asarray(image.convert("RGBA"))
    visible = rgba[..., 3] > 8
    pixels = rgba[..., :3][visible]
    if not len(pixels):
        raise ValueError("No visible pixels")
    # Cluster only visible artwork; transparent black must not consume a palette slot.
    sample = pixels[::max(1, len(pixels) // 100_000)]
    seed = Image.fromarray(sample.reshape(1, -1, 3)).quantize(colors=max_colors,
                                                           method=Image.Quantize.MEDIANCUT)
    colors = np.array(seed.getpalette(), np.uint8).reshape(-1, 3)
    indices = [index for _, index in sorted(seed.getcolors(), reverse=True)]
    selected = colors[indices]
    lab = cv2.cvtColor(selected.reshape(1, -1, 3).astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[0]
    keep = []
    for i in range(len(selected)):
        if not keep or min(np.linalg.norm(lab[i] - lab[j]) for j in keep) > delta_e:
            keep.append(i)
    colors = selected[keep]
    centers = lab[keep]
    labels = np.full(visible.shape, -1, dtype=np.int16)
    # Chunk assignment avoids an H*W*colors full-resolution tensor.
    flat = pixels.astype(np.float32) / 255
    assigned = np.empty(len(pixels), np.int16)
    for start in range(0, len(pixels), 50000):
        end = min(start + 50000, len(pixels))
        chunk = cv2.cvtColor(flat[start:end].reshape(1, -1, 3), cv2.COLOR_RGB2LAB)[0]
        assigned[start:end] = np.argmin(((chunk[:, None] - centers[None]) ** 2).sum(axis=2), axis=1)
    labels[visible] = assigned
    result = rgba.copy()
    result[..., :3][visible] = colors[assigned]
    palette = []
    for i, color in enumerate(colors):
        count = int(np.count_nonzero(assigned == i))
        if not count:
            continue
        palette.append({"id": f"color_{i:02d}", "index": i, "hex": "#" + "".join(f"{v:02x}" for v in color),
                        "rgb": color.tolist(), "pixel_count": count, "fraction": count / len(pixels)})
    return Image.fromarray(result), palette, labels
