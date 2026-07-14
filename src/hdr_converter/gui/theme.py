"""主题：跟随系统 / 浅色 / 深色。"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import Theme, setTheme
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.style_sheet import isDarkTheme, updateStyleSheet

THEME_MODES = (Theme.AUTO, Theme.LIGHT, Theme.DARK)

_LIGHT_BG = QColor("#F3F3F3")
_DARK_BG = QColor("#1E1E1E")
_LIGHT_HEX = "#F3F3F3"
_DARK_HEX = "#1E1E1E"
_NAV_LIGHT = "#F9F9F9"
_NAV_DARK = "#2D2D2D"


def theme_index() -> int:
    return int(QSettings().value("ui/theme", 0, type=int))


def is_effective_dark() -> bool:
    mode = qconfig.themeMode.value
    if mode == Theme.LIGHT:
        return False
    if mode == Theme.DARK:
        return True
    return isDarkTheme()


def _resolve_theme_state() -> None:
    mode = qconfig.themeMode.value
    if mode == Theme.LIGHT:
        qconfig._cfg._theme = Theme.LIGHT
    elif mode == Theme.DARK:
        qconfig._cfg._theme = Theme.DARK
    else:
        qconfig.theme = Theme.AUTO


def apply_theme_index(index: int) -> None:
    index = max(0, min(index, len(THEME_MODES) - 1))
    setTheme(THEME_MODES[index], save=False)
    QSettings().setValue("ui/theme", index)
    _resolve_theme_state()
    updateStyleSheet(False)


def apply_saved_theme() -> None:
    apply_theme_index(theme_index())


def refresh_if_auto() -> None:
    if qconfig.themeMode.value != Theme.AUTO:
        return
    qconfig.theme = Theme.AUTO
    updateStyleSheet(False)
    qconfig.themeChangedFinished.emit()


def _mica_supported() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


def _enable_mica(window, dark: bool) -> None:
    window._isMicaEnabled = True
    if hasattr(window, "windowEffect"):
        try:
            window.windowEffect.setMicaEffect(window.winId(), dark)
        except Exception:
            pass
    window.setBackgroundColor(QColor(0, 0, 0, 0))


def _disable_mica(window) -> None:
    window._isMicaEnabled = False
    if sys.platform == "win32" and hasattr(window, "windowEffect"):
        try:
            window.windowEffect.removeBackgroundEffect(window.winId())
        except Exception:
            pass


def _apply_widget_bg(widget: QWidget, hex_color: str) -> None:
    widget.setStyleSheet(f"background-color: {hex_color};")


def _apply_scroll_area_bg(scroll: QWidget, hex_color: str) -> None:
    _apply_widget_bg(scroll, hex_color)
    viewport = scroll.viewport() if hasattr(scroll, "viewport") else None
    if viewport is not None:
        _apply_widget_bg(viewport, hex_color)


def _iter_page_surfaces(page: QWidget | None):
    """子界面及其内嵌 ScrollArea 上可能带有实心背景样式。"""
    if page is None:
        return
    yield page
    if hasattr(page, "viewport"):
        viewport = page.viewport()
        if viewport is not None:
            yield viewport
    if hasattr(page, "widget"):
        inner = page.widget()
        if inner is not None:
            yield inner
    scroll = getattr(page, "_options_scroll", None) or getattr(page, "_scroll", None)
    if scroll is not None:
        yield scroll
        if hasattr(scroll, "viewport"):
            viewport = scroll.viewport()
            if viewport is not None:
                yield viewport
        if hasattr(scroll, "widget"):
            inner = scroll.widget()
            if inner is not None:
                yield inner


def _clear_solid_overrides(window) -> None:
    if hasattr(window, "stackedWidget"):
        window.stackedWidget.setStyleSheet("")
    if hasattr(window, "navigationInterface"):
        window.navigationInterface.setStyleSheet("background-color: transparent;")
    if hasattr(window, "titleBar"):
        window.titleBar.setStyleSheet("")
    for attr in ("convert_page", "settings_page"):
        page = getattr(window, attr, None)
        for surface in _iter_page_surfaces(page):
            surface.setStyleSheet("")


def _apply_solid_chrome(window, dark: bool) -> None:
    """无 Mica 时（Win10 等）使用实心背景，并保证浅色主题正确。"""
    hex_color = _DARK_HEX if dark else _LIGHT_HEX
    bg = _DARK_BG if dark else _LIGHT_BG
    nav_hex = _NAV_DARK if dark else _NAV_LIGHT

    _disable_mica(window)
    window.setBackgroundColor(bg)

    if hasattr(window, "stackedWidget"):
        window.stackedWidget.setProperty("isTransparent", False)
        _apply_widget_bg(window.stackedWidget, hex_color)

    if hasattr(window, "navigationInterface"):
        window.navigationInterface.setStyleSheet(f"background-color: {nav_hex};")

    if hasattr(window, "titleBar"):
        window.titleBar.setStyleSheet(f"background-color: {hex_color};")

    for attr in ("convert_page", "settings_page"):
        page = getattr(window, attr, None)
        if page is None:
            continue
        for surface in _iter_page_surfaces(page):
            _apply_widget_bg(surface, hex_color)


def sync_window_chrome(window) -> None:
    """Win11 恢复 Mica 透明窗口；浅色/深色按用户选择（非仅系统）。"""
    _resolve_theme_state()
    dark = is_effective_dark()

    if hasattr(window, "setCustomBackgroundColor"):
        window.setCustomBackgroundColor(_LIGHT_BG, _DARK_BG)

    updateStyleSheet(False)

    if _mica_supported():
        _clear_solid_overrides(window)
        _enable_mica(window, dark)
        if hasattr(window, "_updateStackedBackground") and hasattr(window, "stackedWidget"):
            if window.stackedWidget.count() > 0:
                window._updateStackedBackground()
    else:
        _apply_solid_chrome(window, dark)

    window.update()
