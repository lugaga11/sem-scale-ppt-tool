# 项目协作说明

这是合并后的本地 SEM 搜图、测量和 PPTX/SVG 导出工具，主仓库为 `sem-github`。
用户操作和文件树以 [README.md](README.md) 为准，开发流程见 [docs/development.md](docs/development.md)。

## 环境与入口

- 优先用 `D:\Program Files\Anaconda3\envs\my_base\python.exe` 验证。
- 统一入口为 `main.py` / `python -m sem_tool`，打包入口为 `SEMTool.spec`。
- 依赖只维护在 `pyproject.toml`，不要恢复两套 GUI 入口、依赖表或构建配置。

## 维护约定

- `sem_tool/app.py` 负责连接搜索、标注和独立日志页；面板通过信号传递原图路径和日志，不直接修改其他面板内部状态。
- 文件枚举、格式和路径键使用 `files.py`；显示映射/无损转换使用 `imaging.py`。Windows 路径用 `Path`，外部进程使用参数列表。
- 使用完整的规范化路径去重；同名不同目录的图片必须保留。重复导入同图不能清空已有标定、测量或生成勾选。
- 队列保持“生成 / 文件 / 测量”三列及单选，批量测量操作放右键菜单。
- 原图没有标定时必须手动标定，不能猜测物理尺寸；手动标定优先于 TIFF 元数据。
- 测量统一使用 `MeasurementOverride`；只导出用户已应用的标注。膜厚识别仅在编辑器请求时懒加载。
- 新增参数或测量类型时，同时检查 PPTX、SVG、GUI options、worker 和文档。
- 16 位图片导出保留像素值；搜索与预览共用显示映射，不改写原文件。
- SVG 每张图单独保存、仅嵌入一份 PNG；线段使用 `stroke-linecap="butt"`，不加 marker/三角形。保持 PDF 功能移除。
- 索引写入集中在一个任务线程，特征提取可在线程池并行；进度继续限流。
- 跨线程回调使用 `@Slot` 和排队连接，在主线程更新界面。收到 `QThread.finished` 后 `wait()`，再清理线程引用和恢复按钮；程序关闭时先结束搜索并等待导出。
- 保留输入文件/标定/边界的必要校验；删除重复计算、无用兼容层和掩盖编程错误的宽泛异常处理。

## 验证与 Git

```powershell
& 'D:\Program Files\Anaconda3\envs\my_base\python.exe' -m compileall -q main.py sem_tool tests scripts
& 'D:\Program Files\Anaconda3\envs\my_base\python.exe' -m unittest discover -s tests -v
```

界面改动还需查看 Qt 离屏截图；导出改动需检查 PPTX/SVG 内对象、长度、原始像素和命名。
GUI 测试使用实际 Qt 事件循环与计时器驱动对话框和后台任务，避免 `QTest.qWait` 轮询阻塞 Python worker；测试结束停止计时器、释放窗口及剪贴板对象。
检查 `git status` 后再改动，不回滚无关用户修改。样例、生成文件和用户数据保持忽略。
