from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image


def image_to_display_rgb(image: Image.Image) -> Image.Image:
    """The same 8-bit display pixels feed search, thumbnails and editing."""
    if is_high_bit_grayscale(image):
        return _stretch_high_bit(image).convert("RGB")
    if "A" in image.getbands() or "transparency" in image.info:
        rgba = image.convert("RGBA")
        return Image.alpha_composite(Image.new("RGBA", image.size, "white"), rgba).convert("RGB")
    return image.convert("RGB")


HIGH_BIT_GRAYSCALE_MODES = {"I", "I;16", "I;16L", "I;16B", "I;16N"}
PNG_DIRECT_MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}


def is_high_bit_grayscale(image: Image.Image) -> bool:
    if image.mode not in HIGH_BIT_GRAYSCALE_MODES:
        return False
    if image.mode != "I":
        return True
    minimum, maximum = cast(tuple[float, float], image.getextrema())
    return minimum >= 0 and maximum <= 65535


def image_to_display_grayscale(image: Image.Image) -> Image.Image:
    """Return an 8-bit grayscale image without clipping 16-bit SEM intensities."""
    if image.mode == "L":
        return image.copy()
    if is_high_bit_grayscale(image):
        return _stretch_high_bit(image)
    return image.convert("L")


def _stretch_high_bit(image: Image.Image) -> Image.Image:
    """Map the 0.5%-99.5% intensity range of a 16-bit image onto 0-255."""
    integer_image = image.convert("I")
    sample = integer_image.copy()
    sample.thumbnail((512, 512), Image.Resampling.BILINEAR)
    values = np.sort(np.asarray(sample), axis=None)
    if values.size >= 200:
        low = int(values[int((values.size - 1) * 0.005)])
        high = int(values[int((values.size - 1) * 0.995)])
    else:
        low, high = int(values[0]), int(values[-1])
    if high <= low:  # the sample is practically uniform, so use the full range of the image instead
        minimum, maximum = cast(tuple[float, float], integer_image.getextrema())
        low, high = int(minimum), int(maximum)
    if high <= low:
        return Image.new("L", image.size, round(low / 65535 * 255))

    levels = np.arange(65536, dtype=np.int64)
    lookup = np.clip(np.rint((levels - low) * 255 / (high - low)), 0, 255).astype(np.uint8)
    return Image.fromarray(lookup[np.asarray(integer_image)])


def convert_image_to_png(image: Image.Image, output_path: Path) -> None:
    if is_high_bit_grayscale(image):
        if image.mode in {"I;16", "I;16B"}:
            png_image = image
        elif image.mode in {"I;16L", "I;16N"}:
            png_image = Image.frombytes("I;16", image.size, image.tobytes())
        else:
            png_image = image.convert("I;16")
    elif image.mode in PNG_DIRECT_MODES:
        png_image = image
    else:
        png_image = image.convert("RGB")
    png_image.save(output_path, format="PNG")
