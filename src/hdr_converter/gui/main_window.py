"""Fluent 主窗口：DPI 自适应 + i18n。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, QEvent, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QShowEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    ComboBox,
    CompactSpinBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LargeTitleLabel,
    NavigationItemPosition,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SwitchSettingCard,
    PushSettingCard,
    SystemThemeListener,
    TransparentToolButton,
)
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.window import FluentWindow

from ..core.cicp import Gamut, TransferCurve
from ..core.converter import ConvertSettings, convert_batch
from ..core.decode_cache import DecodeCache
from ..core.encoders.base import OutputFormat
from ..core.hdr_options import (
    DEFAULT_GAINMAP_BITS,
    DEFAULT_GAINMAP_SCALE,
    GainMapScale,
    HdrDeliveryMode,
    SdrToneMap,
    default_sdr_tonemap,
    resolve_hdr_delivery,
)
from ..core.jpeg_encode import DEFAULT_JPEG_SUBSAMPLING, JpegSubsampling
from ..core.decoders.jxr_decoder import is_jxr_supported
from .app import create_app
from .format_options_ui import (
    CURVE_KEYS,
    DEFAULT_QUANT_BITS,
    DELIVERY_KEYS,
    GAINMAP_TONEMAP_ORDER,
    JPEG_SUBSAMPLING_ORDER,
    QUANT_KEYS,
    SCALE_KEYS,
    SUBSAMPLING_KEYS,
    TONEMAP_KEYS,
    clamp_quant_bits,
    delivery_card_visible,
    gainmap_options_visible,
    jpeg_subsampling_card_visible,
    quant_card_visible,
    supported_curves,
    supported_delivery_modes,
    supported_quant_bits,
)
from .i18n import Translator, get_translator
from .i18n.translator import SUPPORTED_LOCALES, locale_label_key
from .preview_panel import PreviewPanel, preview_hdr_enabled, set_preview_hdr_enabled
from .preview_worker import PreviewWorker
from .theme import apply_theme_index, refresh_if_auto, sync_window_chrome, theme_index


class ConvertWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        files: list[Path],
        output_dir: Path | None,
        settings: ConvertSettings,
        decode_cache: DecodeCache | None = None,
    ):
        super().__init__()
        self.files = files
        self.output_dir = output_dir
        self.settings = settings
        self.decode_cache = decode_cache

    def run(self) -> None:
        try:
            results = convert_batch(
                self.files,
                self.output_dir,
                self.settings,
                on_progress=lambda cur, total, name: self.progress.emit(cur, total, name),
                decode_cache=self.decode_cache,
            )
            self.finished_ok.emit(results)
        except Exception as exc:
            self.failed.emit(str(exc))


# 侧栏选项行：右侧控件同宽、右边距与 Fluent SettingCard 一致
_CONTROL_WIDTH = 152
_CARD_RIGHT_GAP = 16


class OutputDirSettingCard(SettingCard):
    """可选输出目录；未设置时使用源文件所在目录。"""

    choose_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()

    def __init__(
        self,
        icon: FluentIcon,
        title: str,
        content: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        # 与其它选项行同高：不显示副标题，说明放 tooltip
        super().__init__(icon, title, None, parent)
        self.choose_btn = PushButton("", self)
        self.choose_btn.setFixedSize(_CONTROL_WIDTH, 30)
        self.clear_btn = TransparentToolButton(FluentIcon.CANCEL, self)
        self.clear_btn.setFixedSize(30, 30)
        self.clear_btn.hide()
        self.hBoxLayout.addWidget(self.clear_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(4)
        self.hBoxLayout.addWidget(self.choose_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(_CARD_RIGHT_GAP)
        self.choose_btn.clicked.connect(self.choose_clicked.emit)
        self.clear_btn.clicked.connect(self.clear_clicked.emit)

    def set_choose_text(self, text: str, *, tooltip: str | None = None) -> None:
        """按钮文案；过长则中间省略，完整内容放 tooltip。"""
        fm = self.choose_btn.fontMetrics()
        elided = fm.elidedText(text, Qt.TextElideMode.ElideMiddle, _CONTROL_WIDTH - 20)
        self.choose_btn.setText(elided)
        tip = tooltip or (text if elided != text else "")
        self.choose_btn.setToolTip(tip)


class ComboSettingCard(SettingCard):
    """带 ComboBox 的设置卡片。"""

    def __init__(
        self,
        icon: FluentIcon,
        title: str,
        content: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(icon, title, None, parent)
        self.comboBox = ComboBox(self)
        self.comboBox.setFixedWidth(_CONTROL_WIDTH)
        self.hBoxLayout.addWidget(self.comboBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(_CARD_RIGHT_GAP)

    def fill(self, labels: list[str], *, index: int = 0) -> None:
        self.comboBox.blockSignals(True)
        self.comboBox.clear()
        self.comboBox.addItems(labels)
        self.comboBox.setCurrentIndex(max(0, min(index, self.comboBox.count() - 1)))
        self.comboBox.blockSignals(False)


class EncodeSettingCard(SettingCard):
    """PNG oxipng / 有损质量 切换卡片。"""

    def __init__(
        self,
        icon: FluentIcon,
        title: str,
        content: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(icon, title, None, parent)
        self.comboBox = ComboBox(self)
        self.comboBox.setFixedWidth(_CONTROL_WIDTH)
        self.spinBox = CompactSpinBox(self)
        self.spinBox.setRange(1, 100)
        self.spinBox.setFixedWidth(_CONTROL_WIDTH)
        self.hBoxLayout.addWidget(self.comboBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(self.spinBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(_CARD_RIGHT_GAP)

    def fill_combo(self, labels: list[str], *, index: int = 0) -> None:
        self.comboBox.blockSignals(True)
        self.comboBox.clear()
        self.comboBox.addItems(labels)
        self.comboBox.setCurrentIndex(max(0, min(index, self.comboBox.count() - 1)))
        self.comboBox.blockSignals(False)


def _set_group_title(group: SettingCardGroup, title: str) -> None:
    group.titleLabel.setText(title)


class ConvertInterface(QWidget):
    """转换页：左侧选项面板 + 右侧预览与图片信息栏。"""

    convert_requested = pyqtSignal()

    def __init__(self, tr: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tr = tr
        self._output_dir: Path | None = None
        self._quant_bits = DEFAULT_QUANT_BITS
        self._png_level = 2
        self._lossy_quality = 90
        self._curve = TransferCurve.PQ
        self._hdr_delivery = HdrDeliveryMode.DIRECT
        self._sdr_tonemap = SdrToneMap.HABLE_MAX
        self._gainmap_scale = DEFAULT_GAINMAP_SCALE
        self._jpeg_subsampling = DEFAULT_JPEG_SUBSAMPLING
        self.setObjectName("convertInterface")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        left = QWidget(self)
        left.setFixedWidth(360)
        left_layout = QVBoxLayout(left)
        # 与右侧预览区顶边对齐；水平边距与 sticky / 选项共用，避免按钮比卡片更宽
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(0)

        # 选项卡片相对滚动区/边框再内缩，避免顶满
        _opts_inset = 10

        self._options_scroll = ScrollArea(left)
        self._options_scroll.setWidgetResizable(True)
        self._options_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        options_root = QWidget()
        options_layout = QVBoxLayout(options_root)
        options_layout.setContentsMargins(_opts_inset, 0, _opts_inset, 8)
        options_layout.setSpacing(16)

        # 与「高级」同用 SettingCardGroup 标题，避免 Subtitle + 分组标题两套字号
        self.output_group = SettingCardGroup("", self)
        self.fmt_card = ComboSettingCard(FluentIcon.PHOTO, "", parent=self.output_group)
        self.gamut_card = ComboSettingCard(FluentIcon.PALETTE, "", parent=self.output_group)
        self.curve_card = ComboSettingCard(FluentIcon.BRIGHTNESS, "", parent=self.output_group)
        self.out_card = OutputDirSettingCard(FluentIcon.SAVE, "", parent=self.output_group)
        self.output_group.addSettingCards(
            [self.fmt_card, self.gamut_card, self.curve_card, self.out_card]
        )
        options_layout.addWidget(self.output_group)

        self.adv_group = SettingCardGroup("", self)
        self.delivery_card = ComboSettingCard(FluentIcon.PHOTO, "", parent=self.adv_group)
        self.tonemap_card = ComboSettingCard(FluentIcon.BRIGHTNESS, "", parent=self.adv_group)
        self.gainmap_scale_card = ComboSettingCard(FluentIcon.FULL_SCREEN, "", parent=self.adv_group)
        self.quant_card = ComboSettingCard(FluentIcon.ZOOM, "", parent=self.adv_group)
        self.encode_card = EncodeSettingCard(FluentIcon.SAVE_AS, "", parent=self.adv_group)
        self.jpeg_subsampling_card = ComboSettingCard(
            FluentIcon.PALETTE, "", parent=self.adv_group
        )
        self.embed_icc_card = ComboSettingCard(
            FluentIcon.EMBED, "", parent=self.adv_group
        )
        self.adv_group.addSettingCards(
            [
                self.delivery_card,
                self.tonemap_card,
                self.gainmap_scale_card,
                self.quant_card,
                self.encode_card,
                self.jpeg_subsampling_card,
                self.embed_icc_card,
            ]
        )
        options_layout.addWidget(self.adv_group)
        # 不 stretch：选项紧凑排在上方，底部留白由 ScrollArea 自然产生

        self.fmt_card.comboBox.currentIndexChanged.connect(self._on_format_changed)
        self.curve_card.comboBox.currentIndexChanged.connect(self._on_curve_combo_changed)
        self.delivery_card.comboBox.currentIndexChanged.connect(self._on_delivery_changed)
        self.tonemap_card.comboBox.currentIndexChanged.connect(self._on_hdr_ui_changed)
        self.gainmap_scale_card.comboBox.currentIndexChanged.connect(self._on_hdr_ui_changed)
        self.encode_card.comboBox.currentIndexChanged.connect(self._on_encode_level_changed)
        self.jpeg_subsampling_card.comboBox.currentIndexChanged.connect(
            self._on_jpeg_subsampling_changed
        )
        self.quant_card.comboBox.currentIndexChanged.connect(self._on_quant_changed)
        self.out_card.choose_clicked.connect(self._pick_output_dir)
        self.out_card.clear_clicked.connect(self._clear_output_dir)

        self._options_scroll.setWidget(options_root)
        left_layout.addWidget(self._options_scroll, 1)

        sticky = QWidget(left)
        sticky.setObjectName("convertSticky")
        sticky_layout = QVBoxLayout(sticky)
        # 与选项卡片同宽对齐
        sticky_layout.setContentsMargins(_opts_inset, 12, _opts_inset, 0)
        sticky_layout.setSpacing(10)

        self.progress = ProgressBar()
        self.progress.setVisible(False)
        sticky_layout.addWidget(self.progress)

        self.convert_btn = PrimaryPushButton(FluentIcon.SEND, "")
        self.convert_btn.setMinimumHeight(36)
        self.convert_btn.clicked.connect(self.convert_requested.emit)
        sticky_layout.addWidget(self.convert_btn)
        left_layout.addWidget(sticky)
        outer.addWidget(left)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(0)

        self.preview_panel = PreviewPanel(self)
        self.preview_panel.files_dropped.connect(
            lambda _: self._schedule_preview(immediate=True)
        )
        self.preview_panel.open_files_requested.connect(self._pick_files)
        right_layout.addWidget(self.preview_panel, 1)
        outer.addWidget(right, 1)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        # 选项变更防抖；拖放 / 选文件走 immediate，不经过此处等待。
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._run_preview)
        self._preview_worker: PreviewWorker | None = None
        self._preview_path: Path | None = None
        self._decode_cache = DecodeCache()
        self._force_sdr_preview = False
        self._picking_files = False

        self.retranslate()
        self.gamut_card.comboBox.setCurrentIndex(1)
        self._curve = TransferCurve.PQ
        self.encode_card.spinBox.setValue(90)
        self._update_quant_ui()
        self._update_hdr_delivery_ui()

    def retranslate(self) -> None:
        tr = self._tr
        self.preview_panel.retranslate(tr)
        _set_group_title(self.output_group, tr.tr("panel.options"))
        _set_group_title(self.adv_group, tr.tr("group.advanced"))

        self.fmt_card.titleLabel.setText(tr.tr("label.format"))
        self.gamut_card.titleLabel.setText(tr.tr("label.gamut"))
        self.curve_card.titleLabel.setText(tr.tr("label.curve"))
        self.out_card.titleLabel.setText(tr.tr("label.output_dir"))
        self.out_card.setToolTip(tr.tr("output.dir_hint"))
        self.out_card.choose_btn.setToolTip(tr.tr("output.dir_hint"))
        self.out_card.clear_btn.setToolTip(tr.tr("btn.clear_output"))
        self._refresh_output_button()

        self.quant_card.titleLabel.setText(tr.tr("label.quantize"))
        self.delivery_card.titleLabel.setText(tr.tr("label.hdr_delivery"))
        self.tonemap_card.titleLabel.setText(tr.tr("label.sdr_tonemap"))
        self.gainmap_scale_card.titleLabel.setText(tr.tr("label.gainmap_scale"))
        self.jpeg_subsampling_card.titleLabel.setText(tr.tr("label.jpeg_subsampling"))
        self.embed_icc_card.titleLabel.setText(tr.tr("label.embed_icc"))
        self.embed_icc_card.fill(
            [
                tr.tr("embed_icc.auto"),
                tr.tr("embed_icc.yes"),
                tr.tr("embed_icc.no"),
            ],
            index=self.embed_icc_card.comboBox.currentIndex(),
        )

        self.convert_btn.setText(tr.tr("btn.convert"))

        self.fmt_card.fill(
            [tr.tr(k) for k in ("format.png", "format.heif", "format.avif", "format.jpg", "format.jxl")],
            index=self.fmt_card.comboBox.currentIndex(),
        )
        self.gamut_card.fill(
            [tr.tr(k) for k in ("gamut.srgb", "gamut.p3", "gamut.bt2020")],
            index=self.gamut_card.comboBox.currentIndex(),
        )
        self._update_curve_ui()
        self._update_quant_ui()
        self.encode_card.fill_combo(
            [tr.tr(k) for k in (f"oxipng.{i}" for i in range(7))],
            index=self._png_level,
        )
        self._update_hdr_delivery_ui()
        self._update_jpeg_subsampling_ui()
        self._on_format_curve_changed()

    def _current_output_format(self) -> OutputFormat:
        fmt_map = {
            0: OutputFormat.PNG,
            1: OutputFormat.HEIF,
            2: OutputFormat.AVIF,
            3: OutputFormat.JPG,
            4: OutputFormat.JXL,
        }
        return fmt_map.get(self.fmt_card.comboBox.currentIndex(), OutputFormat.PNG)

    def _update_curve_ui(self) -> None:
        fmt = self._current_output_format()
        curves = supported_curves(fmt)
        if self._curve not in curves:
            self._curve = TransferCurve.PQ if fmt == OutputFormat.JPG else curves[0]
        tr = self._tr
        labels = [tr.tr(CURVE_KEYS[c]) for c in curves]
        idx = curves.index(self._curve)
        self.curve_card.fill(labels, index=idx)
        self._curve = curves[self.curve_card.comboBox.currentIndex()]

    def _sync_curve_from_combo(self) -> None:
        curves = supported_curves(self._current_output_format())
        idx = self.curve_card.comboBox.currentIndex()
        if 0 <= idx < len(curves):
            self._curve = curves[idx]

    def _on_format_changed(self, _index: int | None = None) -> None:
        self._update_curve_ui()
        self._on_format_curve_changed()

    def _on_curve_combo_changed(self, _index: int | None = None) -> None:
        self._sync_curve_from_combo()
        self._on_format_curve_changed()

    def _current_format_curve(self) -> tuple[OutputFormat, TransferCurve]:
        return self._current_output_format(), self._curve

    def _on_delivery_changed(self, index: int) -> None:
        self._on_hdr_ui_changed(index)
        self._update_hdr_delivery_ui()
        self._update_quant_ui()

    def _update_quant_ui(self) -> None:
        fmt, curve = self._current_format_curve()
        visible = quant_card_visible(fmt, self._hdr_delivery)
        self.quant_card.setVisible(visible)
        if fmt in (OutputFormat.HEIF, OutputFormat.AVIF, OutputFormat.JXL):
            self.quant_card.titleLabel.setText(self._tr.tr("label.base_bits"))
        else:
            self.quant_card.titleLabel.setText(self._tr.tr("label.quantize"))
        if not visible:
            return
        bits_list = supported_quant_bits(fmt, curve)
        self._quant_bits = clamp_quant_bits(self._quant_bits, fmt, curve)
        idx = bits_list.index(self._quant_bits)
        labels = [self._tr.tr(QUANT_KEYS[b]) for b in bits_list]
        self.quant_card.fill(labels, index=idx)
        self._quant_bits = bits_list[self.quant_card.comboBox.currentIndex()]

    def _refresh_output_button(self) -> None:
        tr = self._tr
        if self._output_dir is None:
            self.out_card.set_choose_text(tr.tr("output.same_as_source"))
            self.out_card.clear_btn.hide()
            self.out_card.clear_btn.setEnabled(False)
        else:
            path = str(self._output_dir)
            self.out_card.set_choose_text(path, tooltip=path)
            self.out_card.clear_btn.show()
            self.out_card.clear_btn.setEnabled(True)

    def _on_encode_level_changed(self, index: int) -> None:
        self._png_level = index

    def _on_quant_changed(self, index: int) -> None:
        fmt, curve = self._current_format_curve()
        bits_list = supported_quant_bits(fmt, curve)
        if 0 <= index < len(bits_list):
            self._quant_bits = bits_list[index]

    def _on_hdr_ui_changed(self, _index: int | None = None) -> None:
        fmt, curve = self._current_format_curve()
        modes = supported_delivery_modes(fmt, curve)
        d_idx = self.delivery_card.comboBox.currentIndex()
        if 0 <= d_idx < len(modes):
            self._hdr_delivery = modes[d_idx]
        tonemap_list = list(GAINMAP_TONEMAP_ORDER)
        t_idx = self.tonemap_card.comboBox.currentIndex()
        if 0 <= t_idx < len(tonemap_list):
            self._sdr_tonemap = tonemap_list[t_idx]
        scale_list = list(SCALE_KEYS.keys())
        s_idx = self.gainmap_scale_card.comboBox.currentIndex()
        if 0 <= s_idx < len(scale_list):
            self._gainmap_scale = scale_list[s_idx].value

    def _update_hdr_delivery_ui(self) -> None:
        fmt, curve = self._current_format_curve()
        tr = self._tr
        show_delivery = delivery_card_visible(fmt)
        self.delivery_card.setVisible(show_delivery)
        if show_delivery:
            modes = supported_delivery_modes(fmt, curve)
            if self._hdr_delivery not in modes:
                self._hdr_delivery = modes[0]
            labels = [tr.tr(DELIVERY_KEYS[m]) for m in modes]
            idx = modes.index(self._hdr_delivery)
            self.delivery_card.fill(labels, index=idx)
            self._hdr_delivery = modes[self.delivery_card.comboBox.currentIndex()]

        show_gainmap = gainmap_options_visible(fmt, self._hdr_delivery)
        self.tonemap_card.setVisible(show_gainmap)
        self.gainmap_scale_card.setVisible(show_gainmap)

        if show_gainmap:
            if self._sdr_tonemap not in GAINMAP_TONEMAP_ORDER:
                self._sdr_tonemap = default_sdr_tonemap(curve)
            tonemap_list = list(GAINMAP_TONEMAP_ORDER)
            t_labels = [tr.tr(TONEMAP_KEYS[t]) for t in tonemap_list]
            self.tonemap_card.fill(t_labels, index=tonemap_list.index(self._sdr_tonemap))

            scale_list = list(SCALE_KEYS.keys())
            current_scale = GainMapScale(self._gainmap_scale)
            if current_scale not in scale_list:
                current_scale = GainMapScale(DEFAULT_GAINMAP_SCALE)
            s_labels = [tr.tr(SCALE_KEYS[s]) for s in scale_list]
            self.gainmap_scale_card.fill(
                s_labels, index=scale_list.index(current_scale)
            )
            self._gainmap_scale = scale_list[
                self.gainmap_scale_card.comboBox.currentIndex()
            ].value

    def _update_jpeg_subsampling_ui(self) -> None:
        fmt = self._current_output_format()
        visible = jpeg_subsampling_card_visible(fmt)
        self.jpeg_subsampling_card.setVisible(visible)
        if not visible:
            return
        modes = JPEG_SUBSAMPLING_ORDER
        if self._jpeg_subsampling not in modes:
            self._jpeg_subsampling = DEFAULT_JPEG_SUBSAMPLING
        tr = self._tr
        labels = [tr.tr(SUBSAMPLING_KEYS[m]) for m in modes]
        idx = modes.index(self._jpeg_subsampling)
        self.jpeg_subsampling_card.fill(labels, index=idx)
        self._jpeg_subsampling = modes[
            self.jpeg_subsampling_card.comboBox.currentIndex()
        ]

    def _on_jpeg_subsampling_changed(self, _index: int | None = None) -> None:
        modes = JPEG_SUBSAMPLING_ORDER
        idx = self.jpeg_subsampling_card.comboBox.currentIndex()
        if 0 <= idx < len(modes):
            self._jpeg_subsampling = modes[idx]

    def _on_format_curve_changed(self, _index: int | None = None) -> None:
        is_png = self._current_output_format() == OutputFormat.PNG
        tr = self._tr

        if is_png:
            self._lossy_quality = self.encode_card.spinBox.value()
            self.encode_card.titleLabel.setText(tr.tr("label.encode_png"))
            self.encode_card.comboBox.show()
            self.encode_card.spinBox.hide()
            self.encode_card.comboBox.blockSignals(True)
            self.encode_card.comboBox.setCurrentIndex(self._png_level)
            self.encode_card.comboBox.blockSignals(False)
        else:
            self._png_level = self.encode_card.comboBox.currentIndex()
            self.encode_card.titleLabel.setText(tr.tr("label.encode_lossy"))
            self.encode_card.comboBox.hide()
            self.encode_card.spinBox.show()
            self.encode_card.spinBox.blockSignals(True)
            self.encode_card.spinBox.setValue(self._lossy_quality)
            self.encode_card.spinBox.blockSignals(False)

        self._update_quant_ui()
        self._update_hdr_delivery_ui()
        self._update_jpeg_subsampling_ui()
        self._schedule_preview()

    def _schedule_preview(self, *_args, immediate: bool = False, silent: bool = False) -> None:
        files = self.preview_panel.files
        if not files:
            self._preview_path = None
            self._decode_cache.clear()
            self.preview_panel.set_empty(self._tr)
            return
        self._decode_cache.drop_missing(files)
        self._preview_path = files[0]
        if not silent:
            self.preview_panel.set_loading(self._tr)
        if immediate:
            self._preview_timer.stop()
            self._run_preview()
        else:
            self._preview_timer.start()

    def _run_preview(self) -> None:
        path = self._preview_path
        if path is None or not path.is_file():
            self.preview_panel.set_empty(self._tr)
            return
        settings = self.build_settings()
        if self._preview_worker is not None:
            self._preview_worker.cancel()
            # 不 wait：靠 generation 丢弃旧结果，避免拖放连点卡数秒。

        hdr_on = preview_hdr_enabled()
        # HDR 开且未强制 SDR：跳过 Hable，显著加快首帧。
        need_sdr = self._force_sdr_preview or not hdr_on
        need_hdr = True
        self._force_sdr_preview = False

        self._preview_worker = PreviewWorker(
            path,
            gamut=settings.gamut,
            decode_cache=self._decode_cache,
            need_sdr=need_sdr,
            need_hdr=need_hdr,
        )
        self._preview_worker.ready.connect(self._on_preview_ready)
        self._preview_worker.failed.connect(self._on_preview_failed)
        self._preview_worker.start()

    def wait_preview_decode(self, timeout_ms: int = 30_000) -> None:
        """转换前等待当前预览解码完成，以便复用 decode 缓存。"""
        worker = self._preview_worker
        if worker is None or not worker.isRunning():
            return
        worker.wait(timeout_ms)

    def toast_host(self) -> QWidget:
        """InfoBar 宿主：转换页本身；配合 TOP_LEFT，提示落在左侧选项区。

        勿以主窗口为 parent + TOP 居中：会叠在 D3D HDR 预览上，关闭后留下残影。
        """
        return self

    @property
    def decode_cache(self) -> DecodeCache:
        return self._decode_cache

    def _on_preview_ready(self, sdr_scrgb, hdr_scrgb, metadata, _err: str) -> None:
        self.preview_panel.show_frames(sdr_scrgb, hdr_scrgb, metadata)
        # D3D 不可用或 HDR 关且尚无 SDR：补算 SDR（不闪 loading）。
        if self.preview_panel.needs_sdr_rebuild():
            self._force_sdr_preview = True
            self._schedule_preview(immediate=True, silent=True)
            return
        self.preview_panel.retranslate(self._tr)

    def on_preview_hdr_toggled(self) -> None:
        if not preview_hdr_enabled() and self.preview_panel.needs_sdr_rebuild():
            self._force_sdr_preview = True
            self._schedule_preview(immediate=True, silent=True)
            return
        self.preview_panel.refresh_mode()
        self.preview_panel.retranslate(self._tr)

    def _on_preview_failed(self, message: str) -> None:
        self.preview_panel.set_empty(self._tr)
        InfoBar.error(
            title=self._tr.tr("preview.fail.title"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_LEFT,
            duration=5000,
            parent=self.toast_host(),
        )

    def _pick_files(self) -> None:
        # 模态框关闭后点击可能「穿透」到预览区，短时间内忽略重复触发
        if self._picking_files:
            return
        self._picking_files = True
        try:
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                self._tr.tr("dialog.pick_files"),
                "",
                self._tr.tr("filter.images"),
            )
            if paths:
                self.preview_panel.set_files([Path(p) for p in paths])
                self._schedule_preview(immediate=True)
        finally:
            QTimer.singleShot(300, self._end_pick_files)

    def _end_pick_files(self) -> None:
        self._picking_files = False

    def _pick_output_dir(self) -> None:
        start = str(self._output_dir or Path.home())
        d = QFileDialog.getExistingDirectory(
            self, self._tr.tr("dialog.pick_dir"), start
        )
        if d:
            self._output_dir = Path(d)
            self._refresh_output_button()

    def _clear_output_dir(self) -> None:
        self._output_dir = None
        self._refresh_output_button()

    @property
    def output_dir(self) -> Path | None:
        return self._output_dir

    def build_settings(self) -> ConvertSettings:
        fmt, curve = self._current_format_curve()
        gamut_map = {0: Gamut.SRGB, 1: Gamut.P3, 2: Gamut.BT2020}
        is_png = fmt == OutputFormat.PNG
        encode_level = (
            self.encode_card.comboBox.currentIndex()
            if is_png
            else self.encode_card.spinBox.value()
        )
        bits_list = supported_quant_bits(fmt, curve)
        if quant_card_visible(fmt, self._hdr_delivery):
            quantize_bits = bits_list[self.quant_card.comboBox.currentIndex()]
        else:
            quantize_bits = clamp_quant_bits(self._quant_bits, fmt, curve)
        self._on_hdr_ui_changed()
        self._on_jpeg_subsampling_changed()
        delivery = resolve_hdr_delivery(fmt, curve, self._hdr_delivery)
        return ConvertSettings(
            output_format=fmt,
            gamut=gamut_map[self.gamut_card.comboBox.currentIndex()],
            curve=curve,
            quantize_bits=quantize_bits,
            encode_level=encode_level,
            hdr_delivery=delivery,
            base_bits=quantize_bits
            if fmt in (OutputFormat.HEIF, OutputFormat.AVIF, OutputFormat.JXL)
            else 10,
            gainmap_bits=DEFAULT_GAINMAP_BITS,  # 增益图固定 8-bit，与 JPG 一致
            gainmap_scale=self._gainmap_scale,
            sdr_tonemap=self._sdr_tonemap,
            jpeg_subsampling=self._jpeg_subsampling,
            embed_icc=(None, True, False)[self.embed_icc_card.comboBox.currentIndex()],
        )


class SettingsInterface(ScrollArea):
    def __init__(self, tr: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tr = tr
        self.setWidgetResizable(True)
        self.setObjectName("settingsInterface")

        root = QWidget()
        self.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(36, 24, 36, 36)
        layout.setSpacing(20)

        self.page_title = LargeTitleLabel("")
        layout.addWidget(self.page_title)

        self.appear_group = SettingCardGroup("", self)
        self.lang_card = ComboSettingCard(FluentIcon.LANGUAGE, "", parent=self.appear_group)
        self.theme_card = ComboSettingCard(FluentIcon.BRUSH, "", parent=self.appear_group)
        self.appear_group.addSettingCards([self.lang_card, self.theme_card])
        layout.addWidget(self.appear_group)

        self.preview_group = SettingCardGroup("", self)
        self.preview_hdr_card = SwitchSettingCard(
            FluentIcon.VIEW, "", "", parent=self.preview_group
        )
        self.preview_group.addSettingCards([self.preview_hdr_card])
        layout.addWidget(self.preview_group)

        self.lang_card.comboBox.currentIndexChanged.connect(self._on_language_changed)
        self.theme_card.comboBox.currentIndexChanged.connect(self._on_theme_changed)
        self.preview_hdr_card.switchButton.checkedChanged.connect(
            self._on_preview_hdr_changed
        )
        layout.addStretch()

        self.retranslate()
        self._sync_language_combo()
        self._sync_theme_combo()
        self._sync_preview_hdr_switch()

    def _sync_preview_hdr_switch(self) -> None:
        self.preview_hdr_card.switchButton.blockSignals(True)
        self.preview_hdr_card.setChecked(preview_hdr_enabled())
        self.preview_hdr_card.switchButton.blockSignals(False)

    def _on_preview_hdr_changed(self, checked: bool) -> None:
        set_preview_hdr_enabled(checked)
        win = self.window()
        if isinstance(win, MainWindow):
            win.convert_page.on_preview_hdr_toggled()

    def retranslate(self) -> None:
        tr = self._tr
        self.page_title.setText(tr.tr("nav.settings"))
        _set_group_title(self.appear_group, tr.tr("settings.appearance"))
        _set_group_title(self.preview_group, tr.tr("settings.preview"))
        self.lang_card.titleLabel.setText(tr.tr("label.language"))
        self.theme_card.titleLabel.setText(tr.tr("label.theme"))
        self.preview_hdr_card.titleLabel.setText(tr.tr("label.preview_hdr"))
        self.preview_hdr_card.contentLabel.setText(tr.tr("label.preview_hdr_hint"))
        self.preview_hdr_card.contentLabel.show()
        self.preview_hdr_card.setFixedHeight(70)

        lang_idx = self.lang_card.comboBox.currentIndex()
        self.lang_card.fill(
            [tr.tr(locale_label_key(code)) for code in SUPPORTED_LOCALES],
            index=lang_idx,
        )

        theme_idx = self.theme_card.comboBox.currentIndex()
        self.theme_card.fill(
            [tr.tr("theme.auto"), tr.tr("theme.light"), tr.tr("theme.dark")],
            index=theme_idx,
        )

    def _sync_language_combo(self) -> None:
        code = self._tr.locale
        idx = SUPPORTED_LOCALES.index(code) if code in SUPPORTED_LOCALES else 0
        self.lang_card.comboBox.blockSignals(True)
        self.lang_card.comboBox.setCurrentIndex(idx)
        self.lang_card.comboBox.blockSignals(False)

    def _sync_theme_combo(self) -> None:
        idx = theme_index()
        self.theme_card.comboBox.blockSignals(True)
        self.theme_card.comboBox.setCurrentIndex(max(0, min(idx, 2)))
        self.theme_card.comboBox.blockSignals(False)

    def _on_language_changed(self, index: int) -> None:
        if 0 <= index < len(SUPPORTED_LOCALES):
            self._tr.set_locale(SUPPORTED_LOCALES[index])

    def _on_theme_changed(self, index: int) -> None:
        apply_theme_index(index)
        # 获取顶层 MainWindow 并刷新 chrome（themeChangedFinished 也会触发）
        win = self.window()
        if win is not None:
            sync_window_chrome(win)


class MainWindow(FluentWindow):
    def __init__(self, tr: Translator | None = None) -> None:
        super().__init__()
        self._tr = tr or get_translator()
        self._worker: ConvertWorker | None = None

        self.convert_page = ConvertInterface(self._tr, self)
        self.settings_page = SettingsInterface(self._tr, self)

        self.addSubInterface(
            self.convert_page, FluentIcon.PHOTO, self._tr.tr("nav.convert")
        )
        self.addSubInterface(
            self.settings_page,
            FluentIcon.SETTING,
            self._tr.tr("nav.settings"),
            position=NavigationItemPosition.BOTTOM,
        )

        self._configure_navigation()
        self._tr.language_changed.connect(self._retranslate_all)
        self.convert_page.convert_requested.connect(self._start_convert)

        self._apply_window_chrome()
        try:
            qconfig.themeChangedFinished.disconnect()
        except TypeError:
            pass
        qconfig.themeChangedFinished.connect(self._sync_chrome)
        self._start_theme_listener()
        self._retranslate_all()

        if not is_jxr_supported():
            self._show_jxr_warning()

    def _configure_navigation(self) -> None:
        """侧边栏展开时使用浮层覆盖，不挤压右侧选项/预览区。"""
        nav = self.navigationInterface
        # 阈值设得足够大，避免进入 EXPAND（占位列宽）模式
        nav.setMinimumExpandWidth(10_000)
        nav.setExpandWidth(220)
        nav.setAcrylicEnabled(True)

    def _onThemeChangedFinished(self) -> None:
        self._sync_chrome()

    def _updateStackedBackground(self) -> None:
        super()._updateStackedBackground()

    def showEvent(self, event: QShowEvent) -> None:
        sync_window_chrome(self)
        super().showEvent(event)
        sync_window_chrome(self)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            # 从后台切回前台时只重 Present 上一帧，避免整页双线性重算卡顿，
            # 同时清掉 DWM / InfoBar 可能残留的叠层痕迹。
            self.convert_page.preview_panel.represent_preview()

    def _toast_host(self) -> QWidget:
        return self.convert_page.toast_host()

    def _apply_window_chrome(self) -> None:
        self.setMinimumSize(960, 640)
        self.resize(1120, 720)
        self.titleBar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        sync_window_chrome(self)

    def _sync_chrome(self) -> None:
        sync_window_chrome(self)

    def _start_theme_listener(self) -> None:
        self._theme_listener = SystemThemeListener(self)
        self._theme_listener.systemThemeChanged.connect(self._on_system_theme_changed)
        self._theme_listener.start()

    def _on_system_theme_changed(self) -> None:
        refresh_if_auto()
        self._sync_chrome()

    def closeEvent(self, event: QCloseEvent) -> None:
        if hasattr(self, "_theme_listener") and self._theme_listener.isRunning():
            self._theme_listener.terminate()
            self._theme_listener.wait()
        self.convert_page.preview_panel.close_hdr()
        super().closeEvent(event)

    def _retranslate_all(self, _locale: str = "") -> None:
        self._tr = get_translator()
        self.setWindowTitle(self._tr.tr("app.title"))
        self.convert_page.retranslate()
        self.settings_page.retranslate()
        for route_key, key in (
            ("convertInterface", "nav.convert"),
            ("settingsInterface", "nav.settings"),
        ):
            item = self.navigationInterface.widget(route_key)
            item.setText(self._tr.tr(key))

    def _show_jxr_warning(self) -> None:
        InfoBar.warning(
            title=self._tr.tr("warn.jxr.title"),
            content=self._tr.tr("warn.jxr.body"),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_LEFT,
            duration=8000,
            parent=self._toast_host(),
        )

    def _start_convert(self) -> None:
        files = self.convert_page.preview_panel.files
        if not files:
            InfoBar.warning(
                title=self._tr.tr("warn.no_files.title"),
                content=self._tr.tr("warn.no_files.body"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_LEFT,
                duration=3000,
                parent=self._toast_host(),
            )
            return

        self.convert_page.convert_btn.setEnabled(False)
        self.convert_page.progress.setVisible(True)
        self.convert_page.progress.setValue(0)

        settings = self.convert_page.build_settings()
        self.convert_page.wait_preview_decode()
        self._worker = ConvertWorker(
            files,
            self.convert_page.output_dir,
            settings,
            decode_cache=self.convert_page.decode_cache,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, current: int, total: int, name: str) -> None:
        self.convert_page.progress.setValue(int(current / total * 100))

    def _on_finished(self, results: list) -> None:
        self.convert_page.convert_btn.setEnabled(True)
        self.convert_page.progress.setValue(100)
        InfoBar.success(
            title=self._tr.tr("info.done.title"),
            content=self._tr.tr("info.done.body", count=len(results)),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_LEFT,
            duration=5000,
            parent=self._toast_host(),
        )

    def _on_failed(self, message: str) -> None:
        self.convert_page.convert_btn.setEnabled(True)
        InfoBar.error(
            title=self._tr.tr("info.fail.title"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_LEFT,
            duration=8000,
            parent=self._toast_host(),
        )


def run_gui() -> int:
    app = QApplication.instance() or create_app()
    window = MainWindow()
    window.show()
    return app.exec()
