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
  - 负责紧凑文件队列、逐图测厚选择、厚度预览微调、输出格式选择、标尺参数输入、后台生成线程和状态输出栏。
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
- 队列支持添加文件、添加文件夹、拖拽、删除选中、清空、自然排序。
- 队列表格保持 3 列：`生成`、`文件`、`厚度`。
- `生成` 控制是否导出该图；生成时只处理已勾选图片。
- `文件` 显示文件名，完整路径放在 tooltip；同名文件显示为 `文件名 [父文件夹名]`。
- `厚度` 列包含行内 `测厚` 勾选框和 `微调` / `已微调` 按钮。
- 新导入图片默认 `生成=True`、`测厚=False`，避免表面图被误加厚度标记。
- 截面厚度只对同时满足 `生成=True` 且 `测厚=True` 的图片运行。
- 点击行内 `微调` 打开预览窗口，拖动上下两条水平界面线校准厚度；按住 `Ctrl` 滚轮可局部缩放微调。
- 应用手动微调后按钮显示 `已微调`；取消 `测厚` 不删除当前会话内的手动微调值。
- 队列右键菜单提供批量操作：选中设为测厚、选中取消测厚、全部取消测厚、删除选中。
- 输出格式通过 `PPTX` / `SVG` 单选按钮选择，不使用下拉框。
- 标尺位置支持右下、左下、右上、左上。
- 标尺长度支持自动短/中/长，也支持固定值，例如 `1um`、`500nm`。
- 可调参数包括线宽、颜色、字体大小、字体是否加粗、页面边距、横向内缩、纵向内缩。

## PPTX 导出约定

- PPTX 使用 16:9 页面，默认尺寸来自 `DEFAULTS["slide_width_inches"]` 和 `DEFAULTS["slide_height_inches"]`。
- 图片按页面和边距等比例适配。
- 长度标尺横线和文字是 PowerPoint 原生对象，方便在 PowerPoint 中继续编辑。
- 截面厚度标记也应保持可编辑对象，包括竖线、三角箭头和文字。
- 横线不应添加圆角效果。

## SVG 导出约定

- SVG 页面贴合图片比例，不保留 16:9 背景。
- 图片会以 base64 PNG 形式嵌入 SVG，因此 SVG 文件通常会比 TIFF 或 PDF 更大。
- 图片嵌入不会因为 base64 本身损失画质；画质主要取决于前面从源图片转换出的 PNG。
- 标尺横线使用 SVG `<line>`，文字使用 `<text>`。
- 横线应保持 `stroke-linecap="butt"`，不要改成圆角端点。
- 截面厚度标记使用 SVG 可编辑矢量对象：竖向 `<line>`、箭头 `<polygon>` 和厚度 `<text>`。
- 多张图片导出 SVG 时，每张图片生成一个独立 SVG，而不是合并到单个文件中。
- 多图命名格式由 `svg_output_path()` 控制，形如 `输出名_01_原图名.svg`。

## 元数据和标尺计算

工具会尽量从 SEM TIFF 元数据读取像素标定：

- `PixelSize=...`
- `Image Pixel Size = ... nm`
- `Pixel Size = ... nm`

内部统一按 `nm/pixel` 处理。固定标尺值由 `parse_scale_nm()` 解析，支持 `nm`、`um`、`mm` 等单位。自动标尺从 `SCALE_CANDIDATES_NM` 中选择接近目标比例的候选值。

截面厚度识别基于 SEM 图像区域的上下界面：

- 排除底部白色信息栏后分析图像。
- 对多个横向窗口分别计算垂直灰度轮廓。
- 上界面优先吸附到样品顶部附近亮脊峰值。
- 下界面取亮界面脊峰值，而不是偏低的最大负梯度边缘。
- 多窗口结果通过评分和加权中位数融合。
- 厚度标签由 `format_thickness_label()` 统一格式化，当前保留三位小数，例如 `0.773 μm`。
- 手动微调值用 `ThicknessOverride` 记录，并且仅在当前 GUI 会话和当前队列中保留。
- 生成时优先使用 `thickness_overrides`；没有手动值但路径在 `thickness_paths` 中时才调用自动识别。

## 代码维护注意事项

- 修改默认行为优先调整 `sem_scale_ppt.py` 顶部的 `DEFAULTS`。
- GUI 新增参数时，需要同时更新：
  - `GenerateOptions`
  - 控件初始化
  - `generate()` 中组装的 options
  - `GeneratorWorker.run()` 中对 `generate_pptx()` / `generate_svg()` 的调用
  - README 或本文件中的说明
- GUI 队列维护时，保持 `生成`、`文件`、`厚度` 三列表格，不要重新增加独立 `文件夹` 或 `厚度状态` 列，除非用户明确要求。
- 厚度相关批量操作应优先放在队列右键菜单，不要重新占用主界面按钮行。
- 行内测厚控件状态要和 `thickness_paths()`、`thickness_overrides_for_generation()` 保持一致。
- 核心生成函数新增参数时，要同时检查 PPTX 和 SVG 两条导出路径是否都需要支持。
- 保持 PDF 相关功能移除状态，除非明确要求恢复。
- 不要把 SVG 多图导出改回合并文件；Illustrator 拖入多页文件时可能只导入第一页。
- Windows 下 PowerShell 读取中文文件时建议显式使用 `-Encoding UTF8`。
- Windows 下 PowerShell 管道输出 `μ` 可能显示乱码；验证真实字符串时可使用 `encode("unicode_escape")`。

## 验证建议

小改动至少运行：

```text
python -m compileall sem_scale_ppt.py sem_scale_ppt_gui.py
```

涉及导出逻辑时，建议用一张示例图片分别导出 PPTX 和 SVG，重点检查：

- 标尺长度是否合理。
- 标尺位置和内缩是否符合预期。
- 字体大小和加粗状态是否生效。
- SVG 横线端点是否为直角。
- 多张 SVG 是否生成多个独立文件。
- 若涉及截面厚度，检查只有勾选 `测厚` 的图片生成厚度对象，未勾选图片不应包含厚度 `<polygon>`。
- 检查厚度数值是否为三位小数，手动微调后导出数值是否随上下界面线变化。
- 检查 SVG 横向标尺仍为 `stroke-linecap="butt"`。

涉及 GUI 布局时，建议实际启动界面或用 Qt offscreen 检查控件层级，确认左侧队列、右侧参数和底部状态栏位置正确。

涉及 GUI 队列时，建议用 Qt offscreen 检查：

- 导入文件夹后队列表格为 3 列。
- 图片默认 `生成=True`、`测厚=False`。
- 勾选测厚后计数显示 `生成 N / 共 N，测厚 M`。
- 应用微调后行内按钮显示 `已微调`。
- 自然排序后每张图的 `生成` / `测厚` / `已微调` 状态仍跟随原文件。
- 删除选中、清空队列会正确清理对应手动厚度状态。

## Git 注意事项

工作区可能已有用户改动。提交前先查看：

```text
git status --short
git diff --stat
```

只暂存和任务相关的文件。不要回滚用户未明确要求回滚的改动。
