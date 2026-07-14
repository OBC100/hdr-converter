"""QApplication 初始化：DPI 自适应与 Fluent 主题。"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from .theme import apply_saved_theme


FONT_SCALE = 0.88


def _apply_dpi(app: QApplication) -> float:
    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    base = app.font()
    screen = app.primaryScreen()
    scale = 1.0
    if screen is not None:
        dpi = screen.logicalDotsPerInch()
        scale = max(1.0, min(dpi / 96.0, 1.75))
    size = max(8.0, base.pointSizeF() * scale * FONT_SCALE)
    base.setPointSizeF(size)
    app.setFont(base)
    return scale


def create_app(argv: list[str] | None = None) -> QApplication:
    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    app.setOrganizationName("JXRHdrConverter")
    app.setApplicationName("HDR Format Converter")
    _apply_dpi(app)
    apply_saved_theme()
    return app
