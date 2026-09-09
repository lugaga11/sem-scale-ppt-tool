from __future__ import annotations

from pathlib import Path
from typing import cast

from PIL import Image


def image_to_display_rgb(image: Image.Image) -> Image.Image:
    """The same 8-bit display pixels feed search, thumbnails and editing."""
    if is_high_bit_grayscale(image):
        return image_to_display_grayscale(image).convert("RGB")
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
    if not is_high_bit_grayscale(image):
        return image.convert("L")

    integer_image = image.convert("I")
    sample = integer_image.copy()
    sample.thumbnail((512, 512), Image.Resampling.BILINEAR)
    values = sorted(int(value) for value in sample.getdata())
    if not values:
        return Image.new("L", image.size, 0)

    if len(values) >= 200:
        low = values[int((len(values) - 1) * 0.005)]
        high = values[int((len(values) - 1) * 0.995)]
    else:
        low, high = values[0], values[-1]
    low = max(0, min(65535, low))
    high = max(0, min(65535, high))
    if high <= low:
        minimum, maximum = cast(tuple[float, float], integer_image.getextrema())
        low = max(0, min(65535, int(minimum)))
        high = max(0, min(65535, int(maximum)))
    if high <= low:
        level = max(0, min(255, int(round(low / 65535 * 255))))
        return Image.new("L", image.size, level)

    span = high - low
    lut = [
        0 if value <= low else 255 if value >= high else int(round((value - low) * 255 / span))
        for value in range(65536)
    ]
    return integer_image.point(lut, "L")


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
