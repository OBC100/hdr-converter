"""PyInstaller 入口：绝对 import，避免相对导入在单文件 EXE 中失败。"""

from __future__ import annotations

from hdr_converter.main import main

if __name__ == "__main__":
    main()
