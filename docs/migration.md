# 从两个独立项目迁移

合并后的主仓库是 `D:\Codex\python\sem-github`，沿用该仓库的 Git 历史。
搜索代码从相邻“搜图”项目迁入本仓库，运行、安装和打包都不再依赖那个目录。
原“搜图”目录及其中已有未提交改动、Git 记录和本地样例保留，作为原项目留档；不纳入合并工具的源码树或构建产物。

| 原文件 | 合并后位置 |
| --- | --- |
| `搜图/main.py`、`sem_scale_ppt_gui.py` 的启动逻辑 | `main.py`、`sem_tool/app.py` |
| `搜图/image_search/engine.py` | `sem_tool/search/engine.py` |
| `搜图/image_search/index.py` | `sem_tool/search/index.py` |
| `搜图/image_search/settings.py` | `sem_tool/settings.py` |
| `搜图/image_search/ui.py` | `sem_tool/search/ui.py`、`worker.py` |
| `sem_scale_ppt.py` | `sem_tool/annotation/core.py`、`sem_tool/imaging.py` |
| `sem_scale_ppt_gui.py` | `sem_tool/annotation/ui.py`、`preview.py`、`queue.py`、`worker.py` |
| 两套文件发现、格式和打开路径逻辑 | `sem_tool/files.py` |
| `SEM_scale_PPT_tool.spec`、`ImageOriginFinder.spec` | `SEMTool.spec` |
| 原样例校验脚本 | `scripts/verify_sample.py` |

旧的 `sem_scale_ppt.py`、`sem_scale_ppt_gui.py` 和主项目旧 spec 已移除，不保留重复实现。
原来引用核心 API 的脚本改为从 `sem_tool.annotation.core` 导入；路径相关函数从 `sem_tool.files` 导入，图片转换从 `sem_tool.imaging` 导入。

旧搜索索引、上次图库和剪贴板缓存继续使用 `%LOCALAPPDATA%\ImageOriginFinder`，无需迁移数据库。
原标注工具本来就不保存队列会话，因此没有历史测量会话文件需要转换。已有图片、PPTX、SVG 和样例不会被修改。

本次清理包括重复入口和 spec、重复导出参数转发和图片准备、重复格式/路径定义、未使用的测量接口、无效异常兜底、Qt 旧版本位置 API 兼容分支，以及重复数据库建表/连接所有权分支。
同时修复 16 位 TIFF 搜索、失败索引重试、无特征图片状态、状态查询加载完整特征、全局粘贴快捷键冲突和后台线程结束时序。
