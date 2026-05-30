# SEM 标尺 PPT/SVG 工具

用 PySide6 图形界面把 SEM 图片批量生成 PowerPoint 或 SVG，并自动添加可编辑长度标尺。

本项目建议在 VSCode 里使用 `my_base` conda 环境运行。

## 使用方式

在 VSCode 中打开并调试：

```text
sem_scale_ppt_gui.py
```

如需修改启动默认值，编辑 `sem_scale_ppt.py` 顶部的 `DEFAULTS` 配置块。

界面功能：

- 左侧是文件队列，右侧是输出和标尺设置。
- 可将 SEM 图片或文件夹直接拖入文件队列。
- 双击队列文件可打开原始图片。
- 可添加文件、添加文件夹、删除选中、自然排序、清空队列。
- 可取消勾选不需要生成的图片。
- 来自不同文件夹的同名文件会作为不同队列项保留。
- 可在输出格式中选择 `PPTX` 或 `SVG`。
- `生成 PPTX` / `生成 SVG` 会保存到指定输出路径。
- `生成并打开` 会生成临时 PPTX 或 SVG 并打开，由你在对应软件中手动另存为。
- 自动标尺长度支持短/中/长三档，也可固定为 `1um`、`500nm` 这类值。
- 可调整标尺位置、横向/纵向内缩、线宽、颜色、字体大小、是否加粗和页面边距。

右下角标尺时，横向内缩越大越靠左，纵向内缩越大越靠上。

生成 PPTX 时，标尺是 PowerPoint 原生线条和文本框；生成 SVG 时，页面会贴合图片比例，不保留 16:9 背景，图片保持栅格，标尺线和文字用矢量对象绘制，方便拖入 Illustrator 后继续编辑。

如果一次导出多张图片为 SVG，工具同样会为每张图片单独生成一个 SVG，命名格式为 `输出名_01_原图名.svg`、`输出名_02_原图名.svg`。

## 依赖

建议在 `my_base` 环境中安装运行依赖：

```text
python -m pip install PySide6 pillow python-pptx
```

其中 `python-pptx` 用于 PPTX 导出，`PySide6` 用于图形界面，`pillow` 用于读取图片和 SEM TIFF 元数据。SVG 导出不需要额外依赖。

## 元数据

工具会尽量读取 SEM TIFF 元数据：

- `PixelSize=...` 按 `nm/pixel` 处理。
- 支持 `Image Pixel Size = ... nm` 和 `Pixel Size = ... nm`。

当前 GUI 版本不使用模板 PPTX 或标定 CSV。
