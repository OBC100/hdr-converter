"""转换页预览区：拖放 + SDR/HDR 预览合一。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QChildEvent, QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QImage, QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import CaptionLabel, CardWidget, FluentIcon, IconWidget, ImageLabel, StrongBodyLabel

from .hdr_d3d11 import D3D11HdrRenderer
from .hdr_preview_window import HdrPreviewSurface
from .preview_frame import (
    PreviewMetadata,
    fit_size_preserve_aspect,
    scrgb_to_display_uint8,
)


def preview_hdr_enabled() -> bool:
    from PyQt6.QtCore import QSettings

    return QSettings().value("ui/preview_hdr", True, type=bool)


def set_preview_hdr_enabled(enabled: bool) -> None:
    from PyQt6.QtCore import QSettings

    QSettings().setValue("ui/preview_hdr", bool(enabled))


def _format_file_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class ImageInfoBar(QFrame):
    """图片元信息栏：第一行文件名；第二行分辨率、大小、亮度、色彩空间。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("imageInfoBar")
        self.setFixedHeight(64)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 4, 16, 4)
        outer.setSpacing(2)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(20)
        self._filename = CaptionLabel("")
        row1.addWidget(self._filename)
        row1.addStretch()
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(20)
        self._resolution = CaptionLabel("")
        self._file_size = CaptionLabel("")
        self._brightness = CaptionLabel("")
        self._color_space = CaptionLabel("")
        for label in (
            self._resolution,
            self._file_size,
            self._brightness,
            self._color_space,
        ):
            row2.addWidget(label)
        row2.addStretch()
        outer.addLayout(row2)

        for label in (
            self._filename,
            self._resolution,
            self._file_size,
            self._brightness,
            self._color_space,
        ):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path: Path | None = None
        self._metadata: PreviewMetadata | None = None
        self._tr = None

    def set_context(self, tr) -> None:
        self._tr = tr
        self._refresh()

    def set_source(self, path: Path | None, *, file_count: int = 1) -> None:
        self._path = path
        self._file_count = file_count
        if path is None:
            self._metadata = None
        self._refresh()

    def set_metadata(self, metadata: PreviewMetadata | None) -> None:
        self._metadata = metadata
        self._refresh()

    def clear(self) -> None:
        self._path = None
        self._metadata = None
        self._file_count = 0
        self._refresh()

    def _empty(self) -> str:
        if self._tr is None:
            return "—"
        return self._tr.tr("info.empty")

    def _refresh(self) -> None:
        tr = self._tr
        empty = self._empty()
        if tr is None or self._path is None:
            self._filename.setText("")
            self._resolution.setText("")
            self._file_size.setText("")
            self._brightness.setText("")
            self._color_space.setText("")
            return

        name = self._path.name
        if getattr(self, "_file_count", 1) > 1:
            name = tr.tr("drop.count", count=self._file_count) + f" · {name}"

        self._filename.setText(f"{tr.tr('info.filename')}: {name}")

        if self._metadata is not None:
            self._resolution.setText(
                tr.tr(
                    "info.resolution_value",
                    width=self._metadata.width,
                    height=self._metadata.height,
                )
            )
            self._brightness.setText(
                tr.tr(
                    "info.luminance_value",
                    max_cll=self._metadata.max_cll,
                    max_fall=self._metadata.max_fall,
                )
            )
            color_space = self._metadata.color_space or empty
            self._color_space.setText(
                tr.tr("info.colorspace_value", colorspace=color_space)
            )
        else:
            self._resolution.setText(f"{tr.tr('info.resolution')}: {empty}")
            self._brightness.setText(f"{tr.tr('info.luminance')}: {empty}")
            self._color_space.setText(f"{tr.tr('info.colorspace')}: {empty}")

        try:
            size_text = _format_file_size(self._path.stat().st_size)
        except OSError:
            size_text = empty
        self._file_size.setText(tr.tr("info.file_size_value", size=size_text))


class PreviewPanel(CardWidget):
    """拖放多格式图片与 SDR/HDR 预览共用同一区域。"""

    files_dropped = pyqtSignal(list)
    open_files_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._files: list[Path] = []
        self._pick_filters_ready = False
        self._cached_sdr_scrgb: np.ndarray | None = None
        self._cached_hdr: np.ndarray | None = None
        self._last_display_key: tuple | None = None
        self._last_frame_key: tuple | None = None
        self._sdr_qimg: QImage | None = None
        self._sdr_phys: tuple[int, int] = (0, 0)
        # 窗口最大化 / 拖拽缩放会连续触发 resize；仅更新控件布局，不重算像素。
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._apply_preview_display)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.main_stack = QStackedWidget(self)

        self._empty = QWidget(self)
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        empty_layout.setSpacing(10)
        self._empty_icon = IconWidget(FluentIcon.FOLDER_ADD, self._empty)
        self._empty_icon.setFixedSize(48, 48)
        icon_row = QWidget()
        icon_layout = QVBoxLayout(icon_row)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.addWidget(self._empty_icon, 0, Qt.AlignmentFlag.AlignCenter)
        self._empty_hint = StrongBodyLabel("")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_sub = CaptionLabel("")
        self._empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(icon_row)
        empty_layout.addWidget(self._empty_hint)
        empty_layout.addWidget(self._empty_sub)
        self._empty.setCursor(Qt.CursorShape.PointingHandCursor)

        self._preview_host = QWidget(self)
        preview_layout = QVBoxLayout(self._preview_host)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget(self._preview_host)
        self._sdr_page = QWidget(self)
        self._sdr_page_layout = QVBoxLayout(self._sdr_page)
        self._sdr_page_layout.setContentsMargins(0, 0, 0, 0)
        self._sdr_page_layout.addStretch()
        self._sdr = ImageLabel(self._sdr_page)
        self._sdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sdr.setMinimumSize(320, 180)
        self._sdr.setBorderRadius(8, 8, 8, 8)
        self._sdr_page_layout.addWidget(self._sdr, 0, Qt.AlignmentFlag.AlignCenter)
        self._loading_label = CaptionLabel("", self._sdr_page)
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.hide()
        self._sdr_page_layout.addWidget(self._loading_label, 0, Qt.AlignmentFlag.AlignCenter)
        self._sdr_page_layout.addStretch()

        self._hdr_host = QWidget(self)
        self._hdr_host.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._hdr_host.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._hdr_host.setAutoFillBackground(False)
        self._hdr_layout = QVBoxLayout(self._hdr_host)
        self._hdr_layout.setContentsMargins(0, 0, 0, 0)
        self._hdr_error = CaptionLabel("")
        self._hdr_error.setWordWrap(True)
        self._hdr_error.hide()
        self._hdr_layout.addWidget(self._hdr_error)
        self._hdr_center = QWidget(self._hdr_host)
        self._hdr_center_layout = QVBoxLayout(self._hdr_center)
        self._hdr_center_layout.setContentsMargins(0, 0, 0, 0)
        self._hdr_center_layout.addStretch()
        self._hdr_surface_row = QWidget(self._hdr_center)
        self._hdr_surface_row_layout = QVBoxLayout(self._hdr_surface_row)
        self._hdr_surface_row_layout.setContentsMargins(0, 0, 0, 0)
        self._hdr_center_layout.addWidget(
            self._hdr_surface_row, 0, Qt.AlignmentFlag.AlignCenter
        )
        self._hdr_center_layout.addStretch()
        self._hdr_layout.addWidget(self._hdr_center, 1)
        self._hdr_surface: HdrPreviewSurface | None = None
        self._hdr_supported = (
            sys.platform == "win32"
            and D3D11HdrRenderer is not None
            and D3D11HdrRenderer.is_supported()
        )

        self.stack.addWidget(self._sdr_page)
        self.stack.addWidget(self._hdr_host)
        preview_layout.addWidget(self.stack)

        self.main_stack.addWidget(self._empty)
        self.main_stack.addWidget(self._preview_host)
        self.main_stack.setMinimumHeight(240)
        self.main_stack.setCurrentIndex(0)
        self.main_stack.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(self.main_stack, 1)

        self.info_bar = ImageInfoBar(self)
        layout.addWidget(self.info_bar)

        self._install_pick_click_filter(self.main_stack)
        self._pick_filters_ready = True

    @property
    def files(self) -> list[Path]:
        return self._files

    def set_files(self, files: list[Path]) -> None:
        self._files = files
        if files:
            self.info_bar.set_source(files[0], file_count=len(files))
        else:
            self.info_bar.clear()

    def _preview_viewport_size(self) -> tuple[int, int]:
        w = max(320, self._preview_host.width())
        h = max(180, self._preview_host.height())
        return w, h

    def _device_pixel_ratio(self) -> float:
        """当前屏幕缩放系数。"""
        dpr = self.devicePixelRatioF()
        return dpr if dpr > 0 else 1.0

    def _fit_display_size(self, img_w: int, img_h: int) -> tuple[int, int]:
        """视口内等比布局尺寸（逻辑像素）；不改动像素缓冲。"""
        avail_w, avail_h = self._preview_viewport_size()
        return fit_size_preserve_aspect(img_w, img_h, avail_w, avail_h)

    def _apply_preview_display(self) -> None:
        """直接输出 L2 缓冲；控件尺寸适配视口，缩放交给 DWM / Qt。"""
        ref = self._cached_hdr if self._cached_hdr is not None else self._cached_sdr_scrgb
        if ref is None:
            return
        if self.main_stack.currentIndex() != 1:
            return

        sh, sw = ref.shape[:2]
        disp_w, disp_h = self._fit_display_size(sw, sh)
        dpr = self._device_pixel_ratio()
        hdr_on = preview_hdr_enabled()
        layout_key = (disp_w, disp_h, hdr_on, round(dpr, 3))
        frame_key = (id(self._cached_sdr_scrgb), id(self._cached_hdr), hdr_on)
        need_upload = frame_key != self._last_frame_key
        if not need_upload and layout_key == self._last_display_key:
            return
        self._last_display_key = layout_key

        use_hdr = (
            hdr_on
            and self._cached_hdr is not None
            and self._hdr_supported
        )
        if use_hdr:
            if not self._ensure_hdr_widgets():
                if self._cached_sdr_scrgb is None:
                    return
                self._apply_fallback_sdr(
                    self._cached_sdr_scrgb, disp_w, disp_h, rebuild=need_upload
                )
                self._last_frame_key = frame_key
                return
            self.stack.setCurrentIndex(1)
            self._hdr_surface.setFixedSize(disp_w, disp_h)
            if need_upload:
                self._hdr_surface.set_frame(self._cached_hdr, copy=False)
                self._last_frame_key = frame_key
            return

        if self._cached_sdr_scrgb is None:
            # 尚无 SDR 缓冲（HDR 快速路径）；等补算完成。
            return
        self._apply_fallback_sdr(
            self._cached_sdr_scrgb, disp_w, disp_h, rebuild=need_upload
        )
        self._last_frame_key = frame_key

    def _apply_fallback_sdr(
        self,
        sdr_scrgb: np.ndarray,
        disp_w: int,
        disp_h: int,
        *,
        rebuild: bool = True,
    ) -> None:
        """SDR：缓冲原样打成 QImage，用 devicePixelRatio 映射到布局尺寸。"""
        self.stack.setCurrentIndex(0)
        if rebuild or self._sdr_qimg is None:
            sdr_uint8 = scrgb_to_display_uint8(sdr_scrgb)
            phys_h, phys_w = sdr_uint8.shape[:2]
            self._sdr_qimg = QImage(
                sdr_uint8.data, phys_w, phys_h, phys_w * 3, QImage.Format.Format_RGB888
            ).copy()
            self._sdr_phys = (phys_w, phys_h)
        phys_w, phys_h = self._sdr_phys
        dpr_x = phys_w / max(1, disp_w)
        dpr_y = phys_h / max(1, disp_h)
        self._sdr_qimg.setDevicePixelRatio(max(dpr_x, dpr_y))
        self._sdr.setImage(self._sdr_qimg)
        self._sdr.setFixedSize(disp_w, disp_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._cached_hdr is not None or self._cached_sdr_scrgb is not None:
            self._last_display_key = None
            self._resize_timer.start()

    def represent_preview(self) -> None:
        """低成本重绘：复用上一帧，清理 InfoBar / DWM 叠层残影。"""
        if self._hdr_surface is not None and self.stack.currentIndex() == 1:
            self._hdr_surface.represent()
        elif self._cached_hdr is not None or self._cached_sdr_scrgb is not None:
            self._last_display_key = None
            self._apply_preview_display()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and (
            self._cached_hdr is not None or self._cached_sdr_scrgb is not None
        ):
            self._last_display_key = None
            self._resize_timer.start()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _install_pick_click_filter(self, root: QWidget) -> None:
        root.installEventFilter(self)
        for child in root.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event: QEvent) -> bool:  # noqa: N802
        # CardWidget 等可能在 super().__init__ 期间就把 self 装成 filter，
        # 此时 main_stack 尚未创建，必须先短路。
        if not getattr(self, "_pick_filters_ready", False):
            return super().eventFilter(watched, event)

        stack = getattr(self, "main_stack", None)
        if stack is None:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.ChildAdded and isinstance(event, QChildEvent):
            child = event.child()
            if isinstance(child, QWidget) and (
                child is stack or stack.isAncestorOf(child)
            ):
                self._install_pick_click_filter(child)
        elif event.type() == QEvent.Type.MouseButtonRelease:
            if (
                isinstance(event, QMouseEvent)
                and event.button() == Qt.MouseButton.LeftButton
                and isinstance(watched, QWidget)
                and (watched is stack or stack.isAncestorOf(watched))
            ):
                # 消费事件，避免沿父链再次触发（每个祖先都装了同一 filter）
                self.open_files_requested.emit()
                return True
        return super().eventFilter(watched, event)

    def dropEvent(self, event: QDropEvent) -> None:
        from ..core.format_detect import InputFormat, detect_format

        paths = []
        for url in event.mimeData().urls():
            p = Path(url.toLocalFile())
            if not p.is_file():
                continue
            if detect_format(p) != InputFormat.UNKNOWN:
                paths.append(p)
        if paths:
            self._files = paths
            self.info_bar.set_source(self._files[0], file_count=len(self._files))
            self.files_dropped.emit(self._files)

    def retranslate(self, tr) -> None:
        self._empty_hint.setText(tr.tr("drop.hint"))
        self._empty_sub.setText(tr.tr("drop.hint_sub"))
        if self._loading_label.isVisible():
            self._loading_label.setText(tr.tr("preview.loading"))
        self.info_bar.set_context(tr)
        if not self._hdr_supported:
            self._hdr_error.setText(tr.tr("preview.hdr_unavailable"))
        elif self._hdr_surface is not None and self._hdr_surface.init_error:
            self._hdr_error.setText(
                f"{tr.tr('preview.hdr_unavailable')}\n{self._hdr_surface.init_error}"
            )

    def _ensure_hdr_widgets(self) -> bool:
        if self._hdr_surface is not None:
            return not bool(self._hdr_surface.init_error)
        if not self._hdr_supported:
            return False
        try:
            self._hdr_surface = HdrPreviewSurface(self._hdr_surface_row)
            self._hdr_surface.setStyleSheet("background: transparent;")
            self._hdr_surface_row_layout.addWidget(
                self._hdr_surface, 0, Qt.AlignmentFlag.AlignCenter
            )
            return True
        except Exception as exc:
            self._hdr_error.setText(str(exc))
            self._hdr_error.show()
            self._hdr_surface = None
            return False

    def _release_hdr_renderer(self) -> None:
        if self._hdr_surface is not None:
            self._hdr_surface.close_renderer()

    def _show_sdr_view(self) -> None:
        self.stack.setCurrentIndex(1 if self._hdr_supported else 0)
        QTimer.singleShot(0, self._apply_preview_display)

    def _show_hdr_view(self) -> None:
        if not self._hdr_supported:
            self._show_sdr_view()
            return
        if not self._ensure_hdr_widgets():
            self._show_sdr_view()
            return
        self.stack.setCurrentIndex(1)
        self._hdr_error.hide()
        QTimer.singleShot(0, self._apply_preview_display)

    def refresh_mode(self) -> None:
        if self.main_stack.currentIndex() != 1:
            return
        self._last_display_key = None
        self._last_frame_key = None
        if preview_hdr_enabled():
            self._show_hdr_view()
        else:
            self._show_sdr_view()

    def set_loading(self, tr) -> None:
        self.main_stack.setCurrentIndex(1)
        if self._files:
            self.info_bar.set_source(self._files[0], file_count=len(self._files))
            self.info_bar.set_metadata(None)
        self._sdr.clear()
        self._loading_label.setText(tr.tr("preview.loading"))
        self._loading_label.show()
        self._show_sdr_view()

    def set_empty(self, tr) -> None:
        self._resize_timer.stop()
        self._cached_sdr_scrgb = None
        self._cached_hdr = None
        self._last_display_key = None
        self._last_frame_key = None
        self._sdr_qimg = None
        self._sdr_phys = (0, 0)
        self.main_stack.setCurrentIndex(0)
        self._sdr.clear()
        self._loading_label.hide()
        self._release_hdr_renderer()
        if self._files:
            self.info_bar.set_source(self._files[0], file_count=len(self._files))
            self.info_bar.set_metadata(None)
        else:
            self.info_bar.clear()
        self.info_bar.set_context(tr)

    def needs_sdr_rebuild(self) -> bool:
        """当前需要 SDR 缓冲但尚未生成（HDR 快速路径或 D3D 不可用）。"""
        if self._cached_sdr_scrgb is not None:
            return False
        if self._cached_hdr is None:
            return False
        if not preview_hdr_enabled():
            return True
        if not self._hdr_supported:
            return True
        if self._hdr_surface is not None and self._hdr_surface.init_error:
            return True
        if self._hdr_surface is None and not self._ensure_hdr_widgets():
            return True
        return False

    def show_frames(
        self,
        sdr_scrgb: np.ndarray | None,
        hdr_scrgb: np.ndarray | None,
        metadata: PreviewMetadata | None = None,
    ) -> None:
        self._resize_timer.stop()
        # 允许 None：HDR 快速路径可不带 SDR；换文件时清除上一份缓冲。
        self._cached_sdr_scrgb = sdr_scrgb
        self._cached_hdr = hdr_scrgb
        self._last_display_key = None
        self._last_frame_key = None
        self._sdr_qimg = None
        self._sdr_phys = (0, 0)
        self.main_stack.setCurrentIndex(1)
        self._loading_label.hide()
        if self._files:
            self.info_bar.set_source(self._files[0], file_count=len(self._files))
        if metadata is not None:
            self.info_bar.set_metadata(metadata)
        self._apply_preview_display()
        if preview_hdr_enabled():
            self._show_hdr_view()
        else:
            self._show_sdr_view()

    def close_hdr(self) -> None:
        self._resize_timer.stop()
        self._release_hdr_renderer()
        if self._hdr_surface is not None:
            self._hdr_surface.setParent(None)
            self._hdr_surface.deleteLater()
            self._hdr_surface = None
        self._last_display_key = None
        self._last_frame_key = None
