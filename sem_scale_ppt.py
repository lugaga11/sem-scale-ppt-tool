#!/usr/bin/env python3
"""Create PowerPoint slides from SEM images with editable scale bars."""

from __future__ import annotations

import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, cast

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Length, Pt


# =========================
# Default Settings - Edit Here
# =========================
DEFAULTS = {
    # PPT page and GUI size.
    "slide_width_inches": 13.333333,
    "slide_height_inches": 7.5,
    "slide_margin_inches": 0.0,
    "output_filename": "sem_with_scale.pptx",
    "window_width": 600,
    "window_height": 600,
    "queue_min_height": 190,
    # Use auto_scale_preset: "short", "medium", or "long".
    "auto_scale_preset": "medium",
    # fixed_scale is used only when "Auto choose scale length" is unchecked.
    "fixed_scale": "1um",
    # Use scale_position: "bottom-right", "bottom-left", "top-right", or "top-left".
    "scale_position": "bottom-right",
    "bar_color": "white",
    "line_width_pt": 3.0,
    # font_size_pt = 0 means automatic.
    "font_size_pt": 36,
    "inset_x_percent": 8,
    "inset_y_percent": 15,
}

DEFAULT_SLIDE_W = Inches(DEFAULTS["slide_width_inches"])
DEFAULT_SLIDE_H = Inches(DEFAULTS["slide_height_inches"])
DEFAULT_MARGIN_INCHES = DEFAULTS["slide_margin_inches"]
AUTO_SCALE_PRESETS = {
    "short": (0.11, 0.08, 0.14),
    "medium": (0.16, 0.12, 0.20),
    "long": (0.32, 0.22, 0.45),
}
IMAGE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
SCALE_CANDIDATES_NM = [
    10,
    20,
    50,
    100,
    200,
    500,
    1000,
    2000,
    5000,
    10000,
    20000,
    50000,
    100000,
]
UNIT_TO_NM = {
    "fm": 1e-6,
    "pm": 1e-3,
    "nm": 1.0,
    "um": 1000.0,
    "mum": 1000.0,
    "mm": 1_000_000.0,
    "m": 1_000_000_000.0,
}


@dataclass(frozen=True)
class Calibration:
    nm_per_pixel: float
    source: str


@dataclass(frozen=True)
class SlideItem:
    source_path: Path
    png_path: Path
    width_px: int
    height_px: int
    calibration: Calibration
    scale_nm: float
    scale_px: float
    label: str


def decode_metadata_value(value: object) -> str:
    if isinstance(value, bytes):
        candidates = []
        for encoding in ("utf-16le", "utf-8", "latin1"):
            try:
                text = value.decode(encoding, errors="replace")
            except Exception:
                continue
            printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
            score = printable - text.count("\x00") * 5
            candidates.append((score, text))
        return max(candidates, key=lambda item: item[0])[1] if candidates else ""
    if isinstance(value, (tuple, list)):
        return "\n".join(decode_metadata_value(item) for item in value)
    return str(value)


def iter_metadata_text(image: Image.Image) -> Iterable[tuple[str, str]]:
    tag_v2 = getattr(image, "tag_v2", None)
    if tag_v2:
        for key, value in tag_v2.items():
            text = decode_metadata_value(value)
            if text.strip():
                yield f"TIFF tag {key}", text
    for key, value in image.info.items():
        if isinstance(value, (str, bytes)):
            text = decode_metadata_value(value)
            if text.strip():
                yield f"image info {key}", text


def normalize_unit(unit: str) -> str:
    unit = unit.strip().lower()
    return unit.replace("\u00b5", "u").replace("\u03bc", "u")


def parse_sem_metadata(image: Image.Image) -> Calibration | None:
    best: Calibration | None = None
    for source, text in iter_metadata_text(image):
        hitachi = re.search(
            r"(?im)^\s*PixelSize\s*=\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*$",
            text,
        )
        if hitachi:
            value = float(hitachi.group(1))
            if value > 0:
                return Calibration(value, f"{source}: PixelSize")

        for line in re.split(r"[\r\n]+", text):
            clean = line.replace("\x00", "").strip()
            lowered = clean.lower()
            if not clean or "fib" in lowered or "argon" in lowered or "state" in lowered:
                continue
            match = re.search(
                r"\b((?:image\s+)?pixel\s+size)\s*=\s*"
                r"([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*"
                r"(fm|pm|nm|[u\u00b5\u03bc]m|mm|m)\b",
                clean,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            value = float(match.group(2))
            unit = normalize_unit(match.group(3))
            nm_per_pixel = value * UNIT_TO_NM.get(unit, math.nan)
            if nm_per_pixel > 0 and math.isfinite(nm_per_pixel):
                calibration = Calibration(nm_per_pixel, f"{source}: {match.group(1)}")
                if match.group(1).lower().startswith("image"):
                    return calibration
                best = best or calibration
    return best


def parse_scale_nm(value: str) -> float:
    match = re.fullmatch(
        r"\s*([0-9]+(?:\.[0-9]+)?)\s*(fm|pm|nm|[u\u00b5\u03bc]m|mm|m)\s*",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"Invalid scale length: {value!r}. Use values like 500nm or 1um.")
    unit = normalize_unit(match.group(2))
    return float(match.group(1)) * UNIT_TO_NM[unit]


def format_scale_label(scale_nm: float) -> str:
    if scale_nm >= 1000:
        value = scale_nm / 1000.0
        unit = "\u03bcm"
    else:
        value = scale_nm
        unit = "nm"
    number = str(int(round(value))) if abs(value - round(value)) < 1e-9 else f"{value:g}"
    return f"{number} {unit}"


def choose_scale_nm(
    width_px: int,
    nm_per_pixel: float,
    fixed_scale_nm: float | None,
    auto_scale_preset: str,
) -> float:
    if fixed_scale_nm:
        return fixed_scale_nm
    target, minimum, maximum = AUTO_SCALE_PRESETS.get(auto_scale_preset, AUTO_SCALE_PRESETS["medium"])
    scored: list[tuple[int, float, float]] = []
    for candidate in SCALE_CANDIDATES_NM:
        fraction = (candidate / nm_per_pixel) / width_px
        in_range = minimum <= fraction <= maximum
        scored.append((0 if in_range else 1, abs(fraction - target), candidate))
    return min(scored, key=lambda item: (item[0], item[1]))[2]


def find_images(input_paths: Path | Iterable[Path]) -> list[Path]:
    roots = [input_paths] if isinstance(input_paths, Path) else list(input_paths)
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: str(path).lower()))
    images = [path for path in paths if path.suffix.lower() in IMAGE_EXTENSIONS]
    if not images:
        raise ValueError("No supported image files found.")
    return images


def hex_color(value: str) -> str:
    colors = {
        "white": "FFFFFF",
        "black": "000000",
        "red": "FF0000",
        "green": "00AA00",
        "blue": "0066CC",
        "yellow": "FFFF00",
    }
    value = value.strip().lower()
    if value in colors:
        return colors[value]
    value = value.lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return value.upper()
    raise ValueError("Color must be a name like white or a hex value like #FFFFFF")


def convert_image_to_png(image: Image.Image, output_path: Path) -> None:
    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")
    image.save(output_path, format="PNG")


def prepare_slide_item(
    path: Path,
    temp_dir: Path,
    index: int,
    fixed_scale_nm: float | None,
    auto_scale_preset: str,
) -> SlideItem:
    with Image.open(path) as image:
        width_px, height_px = image.size
        calibration = parse_sem_metadata(image)
        if not calibration:
            raise ValueError(
                f"No pixel calibration found for {path.name}. "
                "This GUI version only uses calibration embedded in image metadata."
            )
        png_path = temp_dir / f"{index:05d}_{path.stem}.png"
        convert_image_to_png(image, png_path)
        scale_nm = choose_scale_nm(width_px, calibration.nm_per_pixel, fixed_scale_nm, auto_scale_preset)
        scale_px = scale_nm / calibration.nm_per_pixel
    return SlideItem(
        source_path=path,
        png_path=png_path,
        width_px=width_px,
        height_px=height_px,
        calibration=calibration,
        scale_nm=scale_nm,
        scale_px=scale_px,
        label=format_scale_label(scale_nm),
    )


def fit_image(width_px: int, height_px: int, slide_w: int, slide_h: int, margin: int) -> tuple[int, int, int, int]:
    max_w = slide_w - margin * 2
    max_h = slide_h - margin * 2
    scale = min(max_w / width_px, max_h / height_px)
    image_w = int(round(width_px * scale))
    image_h = int(round(height_px * scale))
    image_x = int(round((slide_w - image_w) / 2))
    image_y = int(round((slide_h - image_h) / 2))
    return image_x, image_y, image_w, image_h


def remove_shape_effects(shape) -> None:
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)
    sp_pr = getattr(shape._element, "spPr", None)
    if sp_pr is None:
        return
    for tag in ("a:effectLst", "a:effectDag", "a:scene3d", "a:sp3d"):
        child = sp_pr.find(qn(tag))
        if child is not None:
            sp_pr.remove(child)


def add_scale_bar(
    slide,
    item: SlideItem,
    image_x: int,
    image_y: int,
    image_w: int,
    image_h: int,
    color: str,
    font_size: int | None,
    position: str,
    inset_x_percent: float,
    inset_y_percent: float,
    line_width_pt: float,
) -> None:
    scale_w = int(round(item.scale_px / item.width_px * image_w))
    inset_x = int(round(image_w * inset_x_percent / 100))
    inset_y = int(round(image_h * inset_y_percent / 100))
    font_pt = font_size or max(10, min(24, int(round(image_w / int(Inches(1)) * 1.4))))
    text_h = int(round(font_pt * 12700 * 1.8))
    text_w = max(scale_w + int(Inches(0.35)), int(Inches(0.75)))
    label_gap = int(round(font_pt * 12700 * 0.25))

    if position == "bottom-left":
        x1 = image_x + inset_x
        y1 = image_y + image_h - inset_y
    elif position == "top-right":
        x1 = image_x + image_w - inset_x - scale_w
        y1 = image_y + inset_y + text_h + label_gap
    elif position == "top-left":
        x1 = image_x + inset_x
        y1 = image_y + inset_y + text_h + label_gap
    else:
        x1 = image_x + image_w - inset_x - scale_w
        y1 = image_y + image_h - inset_y

    x1 = max(image_x, min(x1, image_x + image_w - scale_w))
    y1 = max(image_y + text_h + label_gap, min(y1, image_y + image_h))
    rgb = RGBColor.from_string(color)

    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Emu(x1), Emu(y1), Emu(x1 + scale_w), Emu(y1))
    line.line.color.rgb = rgb
    line.line.width = Pt(line_width_pt)
    remove_shape_effects(line)

    text_x = int(round(x1 + scale_w / 2 - text_w / 2))
    text_y = y1 - text_h - label_gap
    box = slide.shapes.add_textbox(Emu(text_x), Emu(text_y), Emu(text_w), Emu(text_h))
    box.fill.background()
    box.line.fill.background()
    remove_shape_effects(box)
    frame = box.text_frame
    frame.clear()
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    run = paragraph.add_run()
    run.text = item.label
    run.font.bold = True
    run.font.name = "Arial"
    run.font.size = Pt(font_pt)
    run.font.color.rgb = rgb


def build_pptx(
    items: list[SlideItem],
    output: Path,
    slide_w: int,
    slide_h: int,
    margin: int,
    color: str,
    font_size: int | None,
    position: str,
    inset_x_percent: float,
    inset_y_percent: float,
    line_width_pt: float,
) -> None:
    prs = Presentation()
    prs.slide_width = cast(Length, slide_w)
    prs.slide_height = cast(Length, slide_h)
    blank_layout = prs.slide_layouts[6]

    for item in items:
        slide = prs.slides.add_slide(blank_layout)
        image_x, image_y, image_w, image_h = fit_image(item.width_px, item.height_px, slide_w, slide_h, margin)
        slide.shapes.add_picture(str(item.png_path), Emu(image_x), Emu(image_y), width=Emu(image_w), height=Emu(image_h))
        add_scale_bar(
            slide,
            item,
            image_x,
            image_y,
            image_w,
            image_h,
            color,
            font_size,
            position,
            inset_x_percent,
            inset_y_percent,
            line_width_pt,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output))


def generate_pptx(
    input_path: Path | Iterable[Path],
    output_path: Path,
    fixed_scale: str | None = None,
    auto_scale_preset: str = DEFAULTS["auto_scale_preset"],
    position: str = DEFAULTS["scale_position"],
    bar_color: str = DEFAULTS["bar_color"],
    font_size: int | None = None,
    margin_inches: float = DEFAULT_MARGIN_INCHES,
    inset_x_percent: float = DEFAULTS["inset_x_percent"],
    inset_y_percent: float = DEFAULTS["inset_y_percent"],
    line_width_pt: float = DEFAULTS["line_width_pt"],
) -> list[SlideItem]:
    slide_w, slide_h = int(DEFAULT_SLIDE_W), int(DEFAULT_SLIDE_H)
    margin = int(Inches(margin_inches))
    fixed_scale_nm = parse_scale_nm(fixed_scale) if fixed_scale else None
    color = hex_color(bar_color)
    image_paths = find_images(input_path)

    with tempfile.TemporaryDirectory(prefix="sem_scale_ppt_") as temp:
        temp_dir = Path(temp)
        items = [
            prepare_slide_item(path, temp_dir, index, fixed_scale_nm, auto_scale_preset)
            for index, path in enumerate(image_paths, start=1)
        ]
        build_pptx(
            items,
            output_path,
            slide_w,
            slide_h,
            margin,
            color,
            font_size,
            position,
            inset_x_percent,
            inset_y_percent,
            line_width_pt,
        )
    return items
