# 项目协作说明

## 项目定位

这是一个 SEM 图片标尺生成工具，主要用于把 SEM 图像批量导出为带长度标尺的 `PPTX` 或 `SVG` 文件。目标使用场景是先自动生成标尺，再在 PowerPoint 或 Adobe Illustrator 中继续编辑排版。

当前推荐给 Illustrator 使用的是 `SVG` 导出：图片本体以栅格图嵌入，标尺横线和文字用 SVG 矢量对象绘制，导入 Illustrator 后更容易单独编辑。

## 主要文件

- `sem_scale_ppt.py`
  - 核心生成逻辑。
  - 负责读取图片、解析 SEM TIFF 元数据、计算长度标尺和截面厚度、生成 PPTX、生成 SVG。
  - 默认配置集中在文件顶部的 `DEFAULTS`。
- `sem_scale_ppt_gui.py`
  - PySide6 图形界面。
  - 负责紧凑文件队列、逐图测量选择、测膜厚/测距预览编辑、输出格式选择、标尺参数输入、后台生成线程和状态输出栏。
- `README.md`
  - 面向用户的使用说明和依赖说明。
- `SEM_scale_PPT_tool.spec`
  - PyInstaller 打包配置。

## 运行环境

项目建议在 `my_base` conda 环境中运行。

基础依赖：

```text
python -m pip install PySide6 pillow python-pptx
```

依赖用途：

- `PySide6`：图形界面。
- `pillow`：读取图片尺寸、TIFF 元数据，并把图片转换为生成所需的 PNG。
- `python-pptx`：生成可编辑的 PowerPoint 文件。

SVG 导出不依赖额外第三方库。项目曾考虑并测试过 PDF 导出和 `reportlab`，但当前已移除 PDF 功能，优先维护 SVG。

## 常用命令

启动 GUI：

```text
python sem_scale_ppt_gui.py
```

语法检查：

```text
python -m compileall sem_scale_ppt.py sem_scale_ppt_gui.py
```

查看 Git 状态：

```text
git status --short --branch
```

## 当前功能要点

- 支持输入图片类型：`.tif`、`.tiff`、`.png`、`.jpg`、`.jpeg`、`.bmp`。
- GUI 左侧是紧凑图片队列，右侧是输出和标尺参数，底部状态输出栏贯穿左右两侧。
- 队列支持添加文件、添加文件夹、拖拽导入、拖动排序、全选生成、取消全选、删除选中、清空、自然排序。
- 队列表格保持 3 列：`生成`、`文件`、`测量`。
- `生成` 控制是否导出该图；生成时只处理已勾选图片。
- 文件队列行选择保持单选，不支持 `Ctrl` 或 `Shift` 多选。
- `文件` 显示文件名，完整路径和标定来源放在 tooltip；同名文件显示为 `文件名 [父文件夹名]`。
- 导入图片时预检元数据标定；缺少标定时在文件单元格显示警告图标、行内测量编辑按钮显示红色，队列统计和生成前预检都会提示缺标定数量或文件名。
- `测量` 列只包含行内编辑按钮；无标注时显示 `编辑`，有标注时显示 `膜厚`、`测距 2` 或 `膜厚+测距 2`。
- 新导入图片默认 `生成=True`、无测量标注，避免表面图被误加测量对象。
- 生成时只处理 `生成=True` 的图片；其中只有存在会话内测量标注的图片才会额外导出膜厚或测距对象。
- 点击行内按钮会打开统一预览窗口，顶部用 `膜厚` / `测距` 作为添加和编辑工具。
- 打开测量预览时不运行膜厚识别；首次点击 `膜厚` 时才懒加载自动识别并新增或选中膜厚标注。自动识别只用于初始建议或后续 `重置选中`，切换工具本身不写回队列状态。
- 膜厚和测距都走统一标注选择流程：预览始终显示所有标注，选中后可拖动、删除、清空或应用；`取消` 不写回队列状态。
- `测距` 工具支持拖拽任意两点添加距离标注，一张图可保留多条；选中已有测距后可拖动端点调整，按住 `Shift` 可锁定到 0/45/90 度等八个方向。
- 右键菜单提供手动标定、恢复元数据标定、清除选中测量标注和删除选中。
- 输出格式通过 `PPTX` / `SVG` 单选按钮选择，不使用下拉框。
- 标尺位置支持右下、左下、右上、左上。
- 标尺长度支持自动短/中/长，也支持固定值，例如 `1um`、`500nm`；自动候选按 `1/2/5 × 10ⁿ` 动态生成，不设置固定物理长度上下限。
- 可调参数包括线宽、颜色、字体大小、字体是否加粗、页面边距、横向内缩、纵向内缩。

## PPTX 导出约定

- PPTX 使用 16:9 页面，默认尺寸来自 `DEFAULTS["slide_width_inches"]` 和 `DEFAULTS["slide_height_inches"]`。
- 图片按页面和边距等比例适配。
- 长度标尺横线和文字是 PowerPoint 原生对象，方便在 PowerPoint 中继续编辑。
- 截面厚度标记也应保持可编辑对象，包括带原生箭头属性的竖线和文字。
- 横线不应添加圆角效果。

## SVG 导出约定

- SVG 页面贴合图片比例，不保留 16:9 背景。
- 图片会以单份 base64 PNG 数据嵌入 SVG；不要同时在 `href` 和 `xlink:href` 重复写入完整 data URI。
- 图片嵌入不会因为 base64 本身损失画质；画质主要取决于前面从源图片转换出的 PNG。
- 标尺横线使用 SVG `<line>`，文字使用 `<text>`。
- 横线应保持 `stroke-linecap="butt"`，不要改成圆角端点。
- 截面厚度标记使用 SVG 可编辑矢量对象：竖向纯 `<line>` 和厚度 `<text>`。
- 任意测距标记使用 SVG 可编辑矢量对象：纯 `<line>` 和长度 `<text>`，不要使用 SVG marker 或额外三角形，避免不同软件渲染差异。
- 多张图片导出 SVG 时，每张图片生成一个独立 SVG，而不是合并到单个文件中。
- SVG 命名格式由 `svg_output_path()` 控制，单张和多张都形如 `输出名_01_原图名.svg`。

## 元数据和标尺计算

工具会尽量从 SEM TIFF 元数据读取像素标定：

- `PixelSize=...`
- `Image Pixel Size = ... nm`
- `Pixel Size = ... nm`

内部统一按 `nm/pixel` 处理。固定标尺值由 `parse_scale_nm()` 解析，支持 `nm`、`um`、`mm` 等单位。自动标尺由 `dynamic_scale_candidates_nm()` 围绕目标物理长度生成 `1/2/5 × 10ⁿ` 候选，再按短/中/长目标比例选择；标签可按量级显示 `fm`、`pm`、`nm`、`μm`、`mm` 或 `m`。

- GUI 手动标定通过在原图坐标中拖出一条已知长度线并输入实际长度，保存为 `CalibrationOverride(known_length_nm, x1, y1, x2, y2)`。
- `CalibrationOverride.nm_per_pixel` 按 `known_length_nm / pixel_length` 计算；手动标定优先于图片元数据，同时用于横向标尺、膜厚和测距。
- `calibration_overrides` 与 `measurement_overrides` 一样按规范化图片路径传入 PPTX/SVG 两条核心生成路径。
- 取消手动标定时恢复元数据值；若图片没有元数据，则重新进入缺标定状态，不能生成或打开测量预览。

截面厚度识别基于 SEM 图像区域的上下界面：

- 排除底部白色信息栏后分析图像。
- 对多个横向窗口分别计算垂直灰度轮廓。
- 上界面优先吸附到样品顶部附近亮脊峰值。
- 下界面先取亮界面脊峰值；若负梯度后很快出现明显亮度回升，则用后向恢复位置校正到真实底部界面。
- 多窗口结果通过评分和加权中位数融合。
- 长度标签由 `format_length_label()` 统一格式化；当前保留三位小数，例如 `0.773 μm`。
- GUI 会话内测量标注统一用 `MeasurementOverride(kind, x1, y1, x2, y2)` 保存；`kind="thickness"` 表示膜厚，`kind="distance"` 表示任意测距。
- 核心生成函数只接受 `measurement_overrides` 作为测量标注入口，不保留膜厚/测距分支参数。
- 生成时不再从队列状态隐式自动识别膜厚；自动识别只服务于预览中新建或重置膜厚标注。
- PPTX/SVG 导出时 `MeasurementOverride` 统一转换为 `MeasurementSegment` 并走共享绘制函数；PPTX 测量可使用线段原生箭头属性，SVG 测量保持纯线段，不要使用 marker 或额外三角形对象。

## 代码维护注意事项

- 修改默认行为优先调整 `sem_scale_ppt.py` 顶部的 `DEFAULTS`。
- GUI 新增参数时，需要同时更新：
  - `GenerateOptions`
  - 控件初始化
  - `generate()` 中组装的 options
  - `GeneratorWorker.run()` 中对 `generate_pptx()` / `generate_svg()` 的调用
  - README 或本文件中的说明
- GUI 队列维护时，保持 `生成`、`文件`、`测量` 三列表格，不要重新增加独立 `文件夹`、`厚度状态` 或 `测距状态` 列，除非用户明确要求。
- 测量相关批量操作应优先放在队列右键菜单，不要重新占用主界面按钮行。
- 行内测量控件状态要和 `marked_measurement_paths()`、`measurement_overrides_for_generation()` 保持一致。
- 文件标定图标、tooltip 和缺标定计数要与 `metadata_calibrations`、`calibration_overrides`、`calibration_overrides_for_generation()` 保持一致。
- 核心生成函数新增参数时，要同时检查 PPTX 和 SVG 两条导出路径是否都需要支持。
- 新增测量类型时优先扩展 `MeasurementOverride.kind` 到 `MeasurementSegment` 的转换逻辑，复用 PPTX/SVG 的通用线段绘制函数。
- 保持 PDF 相关功能移除状态，除非明确要求恢复。
- 不要把 SVG 多图导出改回合并文件；Illustrator 拖入多页文件时可能只导入第一页。
- Windows 下 PowerShell 读取中文文件时建议显式使用 `-Encoding UTF8`。
- Windows 下 PowerShell 管道输出 `μ` 可能显示乱码；验证真实字符串时可使用 `encode("unicode_escape")`。

## 验证建议

小改动至少运行：

```text
python -m compileall sem_scale_ppt.py sem_scale_ppt_gui.py
python -m unittest discover -s tests -v
```

涉及导出逻辑时，建议用一张示例图片分别导出 PPTX 和 SVG，重点检查：

- 标尺长度是否合理。
- 标尺位置和内缩是否符合预期。
- 字体大小和加粗状态是否生效。
- SVG 横线端点是否为直角。
- 多张 SVG 是否生成多个独立文件。
- 若涉及测量标注，检查只有按钮为 `已标` 的图片生成厚度或测距对象，SVG 中不应包含测量 marker 或测量 `<polygon>`。
- 检查厚度和测距数值是否为三位小数，手动微调或拖动端点后导出数值是否随标注位置变化。
- 检查 SVG 横向标尺仍为 `stroke-linecap="butt"`。
- 检查每个 SVG 只包含一次 `data:image/png;base64,`，避免内嵌图片体积翻倍。
- 用一张无标定元数据的普通 PNG 验证：未手动标定时阻止生成；应用手动标定后 PPTX/SVG 和测量预览都使用覆盖后的 `nm/pixel`。
- 用极小和极大的 `nm/pixel` 验证自动标尺仍来自 `1/2/5 × 10ⁿ` 系列，并保持接近所选短/中/长目标比例。

涉及 GUI 布局时，建议实际启动界面或用 Qt offscreen 检查控件层级，确认左侧队列、右侧参数和底部状态栏位置正确。

涉及 GUI 队列时，建议用 Qt offscreen 检查：

- 导入文件夹后队列表格为 3 列。
- 图片默认 `生成=True`、测量按钮为 `编辑`。
- 无元数据图片显示缺标定警告且测量编辑按钮为红色；应用手动标定后按钮恢复默认/已标样式，警告和缺标定计数消失；恢复元数据标定后按原图实际状态更新。
- 打开测量预览时不应调用膜厚识别；首次点击 `膜厚` 时调用一次，后续重复点击复用已加载的自动建议。
- 应用膜厚/测距后计数显示对应测量数量，行内按钮显示 `膜厚`、`测距 N` 或 `膜厚+测距 N`。
- 自然排序后每张图的 `生成` / 测量摘要状态仍跟随原文件。
- 删除选中、清空队列会正确清理对应手动厚度状态。

## Git 注意事项

工作区可能已有用户改动。提交前先查看：

```text
git status --short
git diff --stat
```

只暂存和任务相关的文件。不要回滚用户未明确要求回滚的改动。
