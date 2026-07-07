#!/usr/bin/env python3
"""Create PowerPoint slides or SVGs from SEM images with editable scale bars."""

from __future__ import annotations

import base64
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence, cast
from xml.sax.saxutils import escape

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
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
    "font_bold": False,
    "inset_x_percent": 8,
    "inset_y_percent": 15,
    "thickness_band_width_percent": 30,
    "thickness_min_gap_percent": 8,
}

DEFAULT_SLIDE_W = Inches(DEFAULTS["slide_width_inches"])
DEFAULT_SLIDE_H = Inches(DEFAULTS["slide_height_inches"])
DEFAULT_MARGIN_INCHES = DEFAULTS["slide_margin_inches"]
POINTS_PER_INCH = 72.0
DEFAULT_PAGE_W_PT = int(round(DEFAULTS["slide_width_inches"] * POINTS_PER_INCH))
DEFAULT_PAGE_H_PT = int(round(DEFAULTS["slide_height_inches"] * POINTS_PER_INCH))
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
class ThicknessMeasurement:
    top_y_px: float
    bottom_y_px: float
    thickness_nm: float
    label: str
    x_center_px: float
    band_x0_px: int
    band_x1_px: int
    confidence: float


@dataclass(frozen=True)
class MeasurementOverride:
    kind: str
    x1_px: float
    y1_px: float
    x2_px: float
    y2_px: float


@dataclass(frozen=True)
class MeasurementSegment:
    x1_px: float
    y1_px: float
    x2_px: float
    y2_px: float
    label: str
    text_side: str = "normal"
    kind: str = "distance"


@dataclass(frozen=True)
class ThicknessCandidate:
    top_y_px: float
    bottom_y_px: float
    x_center_px: float
    band_x0_px: int
    band_x1_px: int
    score: float


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
    measurements: tuple[MeasurementSegment, ...]


@dataclass(frozen=True)
class PageLayout:
    page_w: int
    page_h: int
    image_x: int
    image_y: int
    image_w: int
    image_h: int


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


def format_length_label(length_nm: float) -> str:
    if length_nm >= 100:
        return f"{length_nm / 1000.0:.3f} \u03bcm"
    return f"{length_nm:.3f} nm"


def moving_average(values: list[float], radius: int) -> list[float]:
    if not values:
        return []
    radius = max(0, radius)
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed.append((prefix[end] - prefix[start]) / (end - start))
    return smoothed


def weighted_median(values: list[tuple[float, float]]) -> float:
    if not values:
        raise ValueError("Cannot calculate weighted median for an empty sequence.")
    total = sum(max(0.0, weight) for _, weight in values)
    if total <= 0:
        return sorted(value for value, _ in values)[len(values) // 2]
    threshold = total / 2
    running = 0.0
    sorted_values = sorted(values, key=lambda item: item[0])
    for value, weight in sorted_values:
        running += max(0.0, weight)
        if running >= threshold:
            return value
    return sorted_values[-1][0]


def detect_sem_content_bottom(gray: Image.Image) -> int:
    width, height = gray.size
    pixels = gray.load()
    if pixels is None:
        raise ValueError("Unable to load image pixels.")
    sample_step = max(1, width // 256)
    search_top = int(height * 0.45)
    footer_top = height
    bright_run = 0
    gap_run = 0
    for y in range(height - 1, search_top, -1):
        values = [pixels[x, y] for x in range(0, width, sample_step)]
        mean_value = sum(values) / len(values)
        bright_fraction = sum(value > 230 for value in values) / len(values)
        is_footer_row = mean_value > 185 and bright_fraction > 0.70
        if is_footer_row:
            bright_run += 1
            gap_run = 0
            footer_top = y
        elif bright_run >= 8:
            gap_run += 1
            if gap_run > 4:
                return max(1, footer_top)
        else:
            bright_run = 0
            gap_run = 0
            footer_top = height
    return max(1, footer_top) if bright_run >= 8 else height


def row_profile(gray: Image.Image, x0: int, x1: int, y_end: int) -> list[float]:
    pixels = gray.load()
    if pixels is None:
        raise ValueError("Unable to load image pixels.")
    sample_step = max(1, (x1 - x0) // 256)
    profile: list[float] = []
    for y in range(y_end):
        values = [pixels[x, y] for x in range(x0, x1, sample_step)]
        profile.append(sum(values) / len(values))
    return profile


def detect_thickness_candidate(
    gray: Image.Image,
    x0: int,
    x1: int,
    content_bottom: int,
    min_gap_percent: float,
) -> ThicknessCandidate | None:
    profile = row_profile(gray, x0, x1, content_bottom)
    if len(profile) < 40:
        return None
    smooth = moving_average(profile, max(3, min(12, int(content_bottom * 0.008))))
    gradient = [0.0] * len(smooth)
    for index in range(1, len(smooth) - 1):
        gradient[index] = (smooth[index + 1] - smooth[index - 1]) / 2

    top_start = max(5, int(content_bottom * 0.03))
    top_end = min(content_bottom - 10, max(top_start + 10, int(content_bottom * 0.55)))
    if top_end <= top_start:
        return None
    top_edge_y = max(range(top_start, top_end), key=lambda y: gradient[y])
    top_peak_start = max(0, top_edge_y - 2)
    top_peak_end = min(content_bottom, top_edge_y + 16)
    top_y = max(range(top_peak_start, top_peak_end), key=lambda y: smooth[y])

    min_gap = max(12, int(content_bottom * min_gap_percent / 100))
    lower_start = min(content_bottom - 10, top_y + min_gap)
    lower_end = min(content_bottom - 3, max(lower_start + 10, int(content_bottom * 0.95)))
    if lower_end <= lower_start:
        return None
    lower_edge_y = max(range(lower_start, lower_end), key=lambda y: -gradient[y])
    lower_peak_start = max(lower_start, lower_edge_y - max(8, int(content_bottom * 0.025)))
    lower_peak_end = min(content_bottom, lower_edge_y + 4)
    bottom_y = max(range(lower_peak_start, lower_peak_end), key=lambda y: smooth[y])
    rebound_start = lower_edge_y + 2
    rebound_end = min(content_bottom - 1, lower_edge_y + max(14, int(content_bottom * 0.045)))
    if rebound_end > rebound_start:
        rebound_edge_y = max(range(rebound_start, rebound_end), key=lambda y: gradient[y])
        rebound_peak_start = max(rebound_start, rebound_edge_y - 2)
        rebound_peak_end = min(content_bottom, rebound_edge_y + 8)
        rebound_peak_y = max(range(rebound_peak_start, rebound_peak_end), key=lambda y: smooth[y])
        valley_start = max(lower_start, lower_edge_y - 3)
        valley_end = min(content_bottom, rebound_edge_y + 2)
        valley_y = min(range(valley_start, valley_end), key=lambda y: smooth[y])
        recovery = smooth[rebound_peak_y] - smooth[valley_y]
        if recovery >= 12.0 and smooth[rebound_peak_y] >= smooth[bottom_y] + 8.0:
            bottom_y = rebound_peak_y
    if bottom_y <= top_y:
        return None

    contrast_offset = max(8, int(content_bottom * 0.035))
    top_contrast = smooth[top_y] - smooth[max(0, top_y - contrast_offset)]
    bottom_contrast = smooth[bottom_y] - smooth[min(content_bottom - 1, bottom_y + contrast_offset)]
    edge_strength = max(0.0, gradient[top_edge_y]) + max(0.0, -gradient[lower_edge_y])
    score = top_contrast + bottom_contrast + edge_strength
    if score < 70:
        return None
    return ThicknessCandidate(
        top_y_px=float(top_y),
        bottom_y_px=float(bottom_y),
        x_center_px=(x0 + x1) / 2,
        band_x0_px=x0,
        band_x1_px=x1,
        score=score,
    )


def detect_thickness_measurement(
    image: Image.Image,
    calibration: Calibration,
    band_width_percent: float = DEFAULTS["thickness_band_width_percent"],
    min_gap_percent: float = DEFAULTS["thickness_min_gap_percent"],
) -> ThicknessMeasurement | None:
    gray = image.convert("L")
    width, height = gray.size
    content_bottom = detect_sem_content_bottom(gray)
    if content_bottom < max(60, int(height * 0.2)):
        return None

    band_fraction = max(5.0, min(100.0, float(band_width_percent))) / 100
    band_width = min(width, max(40, int(round(width * band_fraction))))
    half_band = band_width / 2
    center_fractions = (0.35, 0.45, 0.55, 0.65, 0.75)
    centers = sorted(
        {
            int(round(min(max(width * fraction, half_band), width - half_band)))
            for fraction in center_fractions
        }
    )

    candidates: list[ThicknessCandidate] = []
    for center in centers:
        x0 = max(0, int(round(center - half_band)))
        x1 = min(width, int(round(center + half_band)))
        if x1 - x0 < 20:
            continue
        candidate = detect_thickness_candidate(gray, x0, x1, content_bottom, min_gap_percent)
        if candidate:
            candidates.append(candidate)
    if not candidates:
        return None

    best_score = max(candidate.score for candidate in candidates)
    usable = [candidate for candidate in candidates if candidate.score >= max(70.0, best_score * 0.55)]
    if not usable:
        return None
    top_y = weighted_median([(candidate.top_y_px, candidate.score) for candidate in usable])
    bottom_y = weighted_median([(candidate.bottom_y_px, candidate.score) for candidate in usable])
    if bottom_y <= top_y:
        return None

    score_total = sum(candidate.score for candidate in usable)
    x_center = sum(candidate.x_center_px * candidate.score for candidate in usable) / score_total
    best = max(usable, key=lambda candidate: candidate.score)
    thickness_nm = (bottom_y - top_y) * calibration.nm_per_pixel
    return ThicknessMeasurement(
        top_y_px=top_y,
        bottom_y_px=bottom_y,
        thickness_nm=thickness_nm,
        label=format_length_label(thickness_nm),
        x_center_px=x_center,
        band_x0_px=best.band_x0_px,
        band_x1_px=best.band_x1_px,
        confidence=min(1.0, best_score / 180.0),
    )


def measurement_segment_from_override(
    override: MeasurementOverride,
    calibration: Calibration,
    width_px: int,
    height_px: int,
) -> MeasurementSegment | None:
    kind = override.kind if override.kind in {"thickness", "distance"} else "distance"
    x1 = max(0.0, min(float(override.x1_px), float(width_px - 1)))
    y1 = max(0.0, min(float(override.y1_px), float(height_px - 1)))
    x2 = max(0.0, min(float(override.x2_px), float(width_px - 1)))
    y2 = max(0.0, min(float(override.y2_px), float(height_px - 1)))
    if kind == "thickness":
        x_center = max(0.0, min((x1 + x2) / 2, float(width_px - 1)))
        top_y, bottom_y = sorted((y1, y2))
        x1 = x2 = x_center
        y1, y2 = top_y, bottom_y
    length_px = math.hypot(x2 - x1, y2 - y1)
    if length_px <= 0:
        return None
    length_nm = length_px * calibration.nm_per_pixel
    return MeasurementSegment(
        x1_px=x1,
        y1_px=y1,
        x2_px=x2,
        y2_px=y2,
        label=format_length_label(length_nm),
        text_side="right" if kind == "thickness" else "normal",
        kind=kind,
    )


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


def path_key(path: Path) -> str:
    return str(path.resolve()).casefold()


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
    measurement_overrides: Sequence[MeasurementOverride] | None = None,
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
        measurement_inputs = list(measurement_overrides or [])
        measurements = tuple(
            segment
            for override in measurement_inputs
            for segment in [measurement_segment_from_override(override, calibration, width_px, height_px)]
            if segment is not None
        )
    return SlideItem(
        source_path=path,
        png_path=png_path,
        width_px=width_px,
        height_px=height_px,
        calibration=calibration,
        scale_nm=scale_nm,
        scale_px=scale_px,
        label=format_scale_label(scale_nm),
        measurements=measurements,
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


def fit_tight_page(width_px: int, height_px: int, max_page_w: int, max_page_h: int, margin: int) -> PageLayout:
    max_image_w = max_page_w - margin * 2
    max_image_h = max_page_h - margin * 2
    if max_image_w <= 0 or max_image_h <= 0:
        raise ValueError("Page margin is too large for the output page size.")
    scale = min(max_image_w / width_px, max_image_h / height_px)
    image_w = int(round(width_px * scale))
    image_h = int(round(height_px * scale))
    return PageLayout(
        page_w=image_w + margin * 2,
        page_h=image_h + margin * 2,
        image_x=margin,
        image_y=margin,
        image_w=image_w,
        image_h=image_h,
    )


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
    font_bold: bool,
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
    run.font.bold = font_bold
    run.font.name = "Arial"
    run.font.size = Pt(font_pt)
    run.font.color.rgb = rgb


def measurement_segments(item: SlideItem) -> tuple[MeasurementSegment, ...]:
    return item.measurements


def scale_measurement_segment(
    segment: MeasurementSegment,
    item: SlideItem,
    image_x: int,
    image_y: int,
    image_w: int,
    image_h: int,
) -> MeasurementSegment:
    return MeasurementSegment(
        x1_px=image_x + segment.x1_px / item.width_px * image_w,
        y1_px=image_y + segment.y1_px / item.height_px * image_h,
        x2_px=image_x + segment.x2_px / item.width_px * image_w,
        y2_px=image_y + segment.y2_px / item.height_px * image_h,
        label=segment.label,
        text_side=segment.text_side,
        kind=segment.kind,
    )


def measurement_text_candidates(
    segment: MeasurementSegment,
    center_x: float,
    center_y: float,
    ux: float,
    uy: float,
    offset: float,
) -> list[tuple[float, float]]:
    if segment.text_side == "right":
        return [
            (center_x + offset, center_y),
            (center_x - offset, center_y),
            (center_x, center_y),
        ]
    return [
        (center_x - uy * offset, center_y + ux * offset),
        (center_x + uy * offset, center_y - ux * offset),
        (center_x, center_y),
    ]


def fit_text_top_left(
    candidates: list[tuple[float, float]],
    text_w: float,
    text_h: float,
    image_x: float,
    image_y: float,
    image_w: float,
    image_h: float,
) -> tuple[float, float]:
    for candidate_x, candidate_y in candidates:
        proposed_x = candidate_x - text_w / 2
        proposed_y = candidate_y - text_h / 2
        if (
            proposed_x >= image_x
            and proposed_y >= image_y
            and proposed_x + text_w <= image_x + image_w
            and proposed_y + text_h <= image_y + image_h
        ):
            return proposed_x, proposed_y
    center_x, center_y = candidates[-1]
    return (
        max(image_x, min(center_x - text_w / 2, image_x + image_w - text_w)),
        max(image_y, min(center_y - text_h / 2, image_y + image_h - text_h)),
    )


def set_pptx_line_arrowheads(shape) -> None:
    line = shape._element.spPr.get_or_add_ln()
    for tag, attrs in (
        ("a:headEnd", {"type": "triangle"}),
        ("a:tailEnd", {"type": "triangle"}),
    ):
        existing = line.find(qn(tag))
        if existing is not None:
            line.remove(existing)
        arrow = OxmlElement(tag)
        for key, value in attrs.items():
            arrow.set(key, value)
        line.append(arrow)


def add_pptx_measurement_textbox(
    slide,
    label: str,
    text_x: float,
    text_y: float,
    text_w: float,
    text_h: float,
    rgb: RGBColor,
    font_pt: int,
    font_bold: bool,
) -> None:
    box = slide.shapes.add_textbox(
        Emu(int(round(text_x))),
        Emu(int(round(text_y))),
        Emu(int(round(text_w))),
        Emu(int(round(text_h))),
    )
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
    run.text = label
    run.font.bold = font_bold
    run.font.name = "Arial"
    run.font.size = Pt(font_pt)
    run.font.color.rgb = rgb


def add_pptx_measurement_segment(
    slide,
    segment: MeasurementSegment,
    image_x: int,
    image_y: int,
    image_w: int,
    image_h: int,
    color: str,
    font_size: int | None,
    font_bold: bool,
    line_width_pt: float,
) -> None:
    dx = segment.x2_px - segment.x1_px
    dy = segment.y2_px - segment.y1_px
    span = math.hypot(dx, dy)
    if span <= 0:
        return

    rgb = RGBColor.from_string(color)
    font_pt = font_size or max(10, min(24, int(round(image_w / int(Inches(1)) * 1.4))))
    ux = dx / span
    uy = dy / span

    line = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT,
        Emu(int(round(segment.x1_px))),
        Emu(int(round(segment.y1_px))),
        Emu(int(round(segment.x2_px))),
        Emu(int(round(segment.y2_px))),
    )
    line.line.color.rgb = rgb
    line.line.width = Pt(line_width_pt)
    set_pptx_line_arrowheads(line)
    remove_shape_effects(line)

    text_h = int(Pt(font_pt * 1.45))
    text_w = int(max(Pt(font_pt * 3.4), Pt(font_pt * max(3.0, len(segment.label) * 0.66))))
    center_x = (segment.x1_px + segment.x2_px) / 2
    center_y = (segment.y1_px + segment.y2_px) / 2
    candidates = measurement_text_candidates(segment, center_x, center_y, ux, uy, int(Pt(font_pt * 0.85)))
    text_x, text_y = fit_text_top_left(candidates, text_w, text_h, image_x, image_y, image_w, image_h)
    add_pptx_measurement_textbox(
        slide,
        segment.label,
        text_x,
        text_y,
        text_w,
        text_h,
        rgb,
        font_pt,
        font_bold,
    )


def add_pptx_measurement_markers(
    slide,
    item: SlideItem,
    image_x: int,
    image_y: int,
    image_w: int,
    image_h: int,
    color: str,
    font_size: int | None,
    font_bold: bool,
    line_width_pt: float,
) -> None:
    for segment in measurement_segments(item):
        add_pptx_measurement_segment(
            slide,
            scale_measurement_segment(segment, item, image_x, image_y, image_w, image_h),
            image_x,
            image_y,
            image_w,
            image_h,
            color,
            font_size,
            font_bold,
            line_width_pt,
        )


def build_pptx(
    items: list[SlideItem],
    output: Path,
    slide_w: int,
    slide_h: int,
    margin: int,
    color: str,
    font_size: int | None,
    font_bold: bool,
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
            font_bold,
            position,
            inset_x_percent,
            inset_y_percent,
            line_width_pt,
        )
        add_pptx_measurement_markers(
            slide,
            item,
            image_x,
            image_y,
            image_w,
            image_h,
            color,
            font_size,
            font_bold,
            line_width_pt,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output))


def safe_filename_part(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return cleaned or "image"


def split_output_path(output_path: Path, item: SlideItem, index: int, total: int, suffix: str) -> Path:
    output_path = output_path.with_suffix(suffix)
    stem = safe_filename_part(item.source_path.stem)
    return output_path.with_name(f"{output_path.stem}_{index:02d}_{stem}{suffix}")


def svg_output_path(output_path: Path, item: SlideItem, index: int, total: int) -> Path:
    return split_output_path(output_path, item, index, total, ".svg")


def svg_num(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def svg_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def add_svg_scale_bar(
    parts: list[str],
    item: SlideItem,
    image_x: int,
    image_y: int,
    image_w: int,
    image_h: int,
    color: str,
    font_size: int | None,
    font_bold: bool,
    position: str,
    inset_x_percent: float,
    inset_y_percent: float,
    line_width_pt: float,
) -> None:
    scale_w = int(round(item.scale_px / item.width_px * image_w))
    inset_x = int(round(image_w * inset_x_percent / 100))
    inset_y = int(round(image_h * inset_y_percent / 100))
    font_pt = font_size or max(10, min(24, int(round(image_w / POINTS_PER_INCH * 1.4))))
    text_h = font_pt * 1.8
    label_gap = font_pt * 0.25

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
    text_y = y1 - label_gap - font_pt * 0.35
    svg_color = f"#{color}"
    font_weight = "700" if font_bold else "400"
    label = svg_attr(item.label)

    parts.append(
        f'  <line x1="{svg_num(x1)}" y1="{svg_num(y1)}" '
        f'x2="{svg_num(x1 + scale_w)}" y2="{svg_num(y1)}" '
        f'stroke="{svg_color}" stroke-width="{svg_num(line_width_pt)}" '
        'stroke-linecap="butt" />'
    )
    parts.append(
        f'  <text x="{svg_num(x1 + scale_w / 2)}" y="{svg_num(text_y)}" '
        f'fill="{svg_color}" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="{svg_num(font_pt)}" font-weight="{font_weight}" text-anchor="middle">{label}</text>'
    )


def add_svg_measurement_segment(
    parts: list[str],
    segment: MeasurementSegment,
    image_x: int,
    image_y: int,
    image_w: int,
    image_h: int,
    color: str,
    font_size: int | None,
    font_bold: bool,
    line_width_pt: float,
) -> None:
    dx = segment.x2_px - segment.x1_px
    dy = segment.y2_px - segment.y1_px
    span = math.hypot(dx, dy)
    if span <= 0:
        return

    font_pt = font_size or max(10, min(24, int(round(image_w / POINTS_PER_INCH * 1.4))))
    svg_color = f"#{color}"
    font_weight = "700" if font_bold else "400"
    ux = dx / span
    uy = dy / span

    parts.append(
        f'  <line x1="{svg_num(segment.x1_px)}" y1="{svg_num(segment.y1_px)}" '
        f'x2="{svg_num(segment.x2_px)}" y2="{svg_num(segment.y2_px)}" '
        f'stroke="{svg_color}" stroke-width="{svg_num(line_width_pt)}" '
        'stroke-linecap="butt" />'
    )

    text_w = font_pt * max(3.4, len(segment.label) * 0.66)
    text_h = font_pt * 1.45
    center_x = (segment.x1_px + segment.x2_px) / 2
    center_y = (segment.y1_px + segment.y2_px) / 2
    text_left, text_top = fit_text_top_left(
        measurement_text_candidates(segment, center_x, center_y, ux, uy, font_pt * 0.85),
        text_w,
        text_h,
        image_x,
        image_y,
        image_w,
        image_h,
    )
    parts.append(
        f'  <text x="{svg_num(text_left + text_w / 2)}" y="{svg_num(text_top + font_pt)}" '
        f'fill="{svg_color}" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="{svg_num(font_pt)}" font-weight="{font_weight}" '
        f'text-anchor="middle">{svg_attr(segment.label)}</text>'
    )


def add_svg_measurement_markers(
    parts: list[str],
    item: SlideItem,
    image_x: int,
    image_y: int,
    image_w: int,
    image_h: int,
    color: str,
    font_size: int | None,
    font_bold: bool,
    line_width_pt: float,
) -> None:
    for segment in measurement_segments(item):
        add_svg_measurement_segment(
            parts,
            scale_measurement_segment(segment, item, image_x, image_y, image_w, image_h),
            image_x,
            image_y,
            image_w,
            image_h,
            color,
            font_size,
            font_bold,
            line_width_pt,
        )


def build_svg(
    item: SlideItem,
    output: Path,
    max_page_w: int,
    max_page_h: int,
    margin: int,
    color: str,
    font_size: int | None,
    font_bold: bool,
    position: str,
    inset_x_percent: float,
    inset_y_percent: float,
    line_width_pt: float,
) -> None:
    layout = fit_tight_page(item.width_px, item.height_px, max_page_w, max_page_h, margin)
    image_data = base64.b64encode(item.png_path.read_bytes()).decode("ascii")
    image_href = f"data:image/png;base64,{image_data}"

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{svg_num(layout.page_w)}pt" height="{svg_num(layout.page_h)}pt" '
            f'viewBox="0 0 {svg_num(layout.page_w)} {svg_num(layout.page_h)}">'
        ),
    ]
    parts.append(
        f'  <image x="{svg_num(layout.image_x)}" y="{svg_num(layout.image_y)}" '
        f'width="{svg_num(layout.image_w)}" height="{svg_num(layout.image_h)}" '
        f'href="{image_href}" xlink:href="{image_href}" preserveAspectRatio="none" />'
    )
    add_svg_scale_bar(
        parts,
        item,
        layout.image_x,
        layout.image_y,
        layout.image_w,
        layout.image_h,
        color,
        font_size,
        font_bold,
        position,
        inset_x_percent,
        inset_y_percent,
        line_width_pt,
    )
    add_svg_measurement_markers(
        parts,
        item,
        layout.image_x,
        layout.image_y,
        layout.image_w,
        layout.image_h,
        color,
        font_size,
        font_bold,
        line_width_pt,
    )
    parts.append("</svg>")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def generate_pptx(
    input_path: Path | Iterable[Path],
    output_path: Path,
    fixed_scale: str | None = None,
    auto_scale_preset: str = DEFAULTS["auto_scale_preset"],
    position: str = DEFAULTS["scale_position"],
    bar_color: str = DEFAULTS["bar_color"],
    font_size: int | None = None,
    font_bold: bool = DEFAULTS["font_bold"],
    margin_inches: float = DEFAULT_MARGIN_INCHES,
    inset_x_percent: float = DEFAULTS["inset_x_percent"],
    inset_y_percent: float = DEFAULTS["inset_y_percent"],
    line_width_pt: float = DEFAULTS["line_width_pt"],
    measurement_overrides: Mapping[Path, Sequence[MeasurementOverride]] | None = None,
) -> list[SlideItem]:
    slide_w, slide_h = int(DEFAULT_SLIDE_W), int(DEFAULT_SLIDE_H)
    margin = int(Inches(margin_inches))
    fixed_scale_nm = parse_scale_nm(fixed_scale) if fixed_scale else None
    color = hex_color(bar_color)
    image_paths = find_images(input_path)
    measurement_override_map = {path_key(path): tuple(overrides) for path, overrides in (measurement_overrides or {}).items()}

    with tempfile.TemporaryDirectory(prefix="sem_scale_ppt_") as temp:
        temp_dir = Path(temp)
        items = [
            prepare_slide_item(
                path,
                temp_dir,
                index,
                fixed_scale_nm,
                auto_scale_preset,
                measurement_override_map.get(path_key(path), ()),
            )
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
            font_bold,
            position,
            inset_x_percent,
            inset_y_percent,
            line_width_pt,
        )
    return items


def generate_svg(
    input_path: Path | Iterable[Path],
    output_path: Path,
    fixed_scale: str | None = None,
    auto_scale_preset: str = DEFAULTS["auto_scale_preset"],
    position: str = DEFAULTS["scale_position"],
    bar_color: str = DEFAULTS["bar_color"],
    font_size: int | None = None,
    font_bold: bool = DEFAULTS["font_bold"],
    margin_inches: float = DEFAULT_MARGIN_INCHES,
    inset_x_percent: float = DEFAULTS["inset_x_percent"],
    inset_y_percent: float = DEFAULTS["inset_y_percent"],
    line_width_pt: float = DEFAULTS["line_width_pt"],
    measurement_overrides: Mapping[Path, Sequence[MeasurementOverride]] | None = None,
) -> list[SlideItem]:
    page_w, page_h = DEFAULT_PAGE_W_PT, DEFAULT_PAGE_H_PT
    margin = int(round(margin_inches * POINTS_PER_INCH))
    fixed_scale_nm = parse_scale_nm(fixed_scale) if fixed_scale else None
    color = hex_color(bar_color)
    image_paths = find_images(input_path)
    measurement_override_map = {path_key(path): tuple(overrides) for path, overrides in (measurement_overrides or {}).items()}

    with tempfile.TemporaryDirectory(prefix="sem_scale_svg_") as temp:
        temp_dir = Path(temp)
        items = [
            prepare_slide_item(
                path,
                temp_dir,
                index,
                fixed_scale_nm,
                auto_scale_preset,
                measurement_override_map.get(path_key(path), ()),
            )
            for index, path in enumerate(image_paths, start=1)
        ]
        total = len(items)
        for index, item in enumerate(items, start=1):
            build_svg(
                item,
                svg_output_path(output_path, item, index, total),
                page_w,
                page_h,
                margin,
                color,
                font_size,
                font_bold,
                position,
                inset_x_percent,
                inset_y_percent,
                line_width_pt,
            )
    return items
