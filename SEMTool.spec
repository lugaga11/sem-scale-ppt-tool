# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

project = Path(SPECPATH)
# Resolve DLLs against Python and Windows, not unrelated programs on the host PATH.
# For example, Poppler's icuuc.dll has versioned exports incompatible with QtCore.
if sys.platform == "win32":
    python_root = Path(sys.base_prefix)
    windows_root = Path(os.environ["SystemRoot"])
    os.environ["PATH"] = os.pathsep.join(map(str, [
        python_root,
        python_root / "Library" / "bin",
        python_root / "Scripts",
        windows_root / "System32",
        windows_root,
    ]))

a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    datas=[(str(project / "sem_tool" / "assets"), "sem_tool/assets")],
    # Unrelated scientific/notebook stacks in conda must not enter the GUI build.
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "IPython", "jupyter", "matplotlib", "scipy", "pandas", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SEMTool",
    console=os.environ.get("SEM_TOOL_CONSOLE") == "1",
    upx=False,
)
