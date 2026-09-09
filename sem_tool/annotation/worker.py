from __future__ import annotations

import math
from pathlib import Path
from typing import TypedDict

from PySide6.QtCore import QObject, Signal, Slot

from .core import CalibrationOverride, MeasurementOverride, generate_pptx, generate_svg, svg_output_path


class GenerateOptions(TypedDict):
    input_path: list[Path]
    output_path: Path
    output_format: str
    fixed_scale: str | None
    auto_scale_preset: str
    position: str
    bar_color: str
    font_size: int | None
    font_bold: bool
    margin_inches: float
    inset_x_percent: float
    inset_y_percent: float
    line_width_pt: float
    measurement_overrides: dict[Path, list[MeasurementOverride]]
    calibration_overrides: dict[Path, CalibrationOverride]


class GeneratorWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)
    log = Signal(str)
    done = Signal()

    def __init__(self, options: GenerateOptions) -> None:
        super().__init__()
        self.options = options

    @Slot()
    def run(self) -> None:
        try:
            output_format = self.options["output_format"]
            self.log.emit(f"正在生成 {output_format.upper()}...")
            arguments = dict(self.options)
            arguments.pop("output_format")
            generator = {"pptx": generate_pptx, "svg": generate_svg}[output_format]
            items = generator(**arguments)
            if output_format == "svg":
                output_paths = [
                    svg_output_path(self.options["output_path"], item, index)
                    for index, item in enumerate(items, start=1)
                ]
            else:
                output_paths = [self.options["output_path"]]
            if len(output_paths) == 1:
                self.log.emit(f"已生成: {output_paths[0]}")
            else:
                self.log.emit(f"已生成 {len(output_paths)} 个 {output_format.upper()}:")
                for path in output_paths:
                    self.log.emit(f"  {path}")
            for item in items:
                self.log.emit(
                    f"{item.source_path.name}: {item.calibration.nm_per_pixel:g} nm/px, "
                    f"标尺 {item.label} = {item.scale_px:.1f} px，标定来源 {item.calibration.source}"
                )
                distance_index = 0
                for measurement in item.measurements:
                    length_px = math.hypot(
                        measurement.x2_px - measurement.x1_px,
                        measurement.y2_px - measurement.y1_px,
                    )
                    if measurement.kind == "thickness":
                        self.log.emit(f"  膜厚: {measurement.label} = {length_px:.1f} px")
                    else:
                        distance_index += 1
                        self.log.emit(f"  测距 {distance_index}: {measurement.label} = {length_px:.1f} px")
            self.finished.emit([str(path) for path in output_paths])
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.done.emit()
