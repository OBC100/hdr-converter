"""各输出格式的 ICC 嵌入策略与统一解析入口。

生成器分两类（禁止「只改 XYZ、不重建 LUT」）：

1. **HDR 显示 ICC**（``libjxl_pq_icc``）：移植 libjxl ``MaybeCreateProfile``，
   含 per-gamut mft1 tone-map LUT（PQ/HLG/Linear → SDR Lab）。
   供 PNG iCCP、Ultra HDR 副图、可选 HEIF/AVIF ``colr``/``prof``、JXL ``alt_icc``。
2. **SDR baseline ICC**（``apple_baseline_icc``）：Apple/Lightroom matrix-shaper，
   sRGB 型 para TRC + 按色域的 r/g/bXYZ。供 PNG SDR、JPG Direct、Ultra HDR 主图。

HEIF/AVIF 默认**只写 NCLX、不写 ICC**：Windows 照片对 ``colr``/``prof`` 易全红/崩溃。
需要跨查看器注明色彩时，用 ``embed_icc=True`` 显式打开。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .assets import get_hdr_icc, get_hdr_icc_name, should_embed_hdr_icc
from .baseline_icc import get_baseline_display_icc
from .cicp import Gamut, TransferCurve, is_hdr_curve
from .encoders.base import OutputFormat
from .hdr_options import HdrDeliveryMode, uses_gainmap


class IccKind(str, Enum):
    """选用哪一类生成器。"""

    HDR = "hdr"  # libjxl MaybeCreateProfile（含 tone-map LUT）
    BASELINE = "baseline"  # Apple matrix-shaper（sRGB γ）
    NONE = "none"


@dataclass(frozen=True)
class IccEmbedPlan:
    """某次编码应写入的 ICC 计划。"""

    kind: IccKind
    embed: bool
    """是否写入容器。"""
    profile_name: str | None
    """PNG iCCP 名称等；baseline 可用 desc 标签。"""
    windows_photos_safe: bool
    """在 Windows 照片中已知安全（不致全红/崩溃）。"""
    reason: str


_BASELINE_NAME: dict[Gamut, str] = {
    Gamut.SRGB: "sRGB",
    Gamut.P3: "Display P3",
    Gamut.BT2020: "Rec. 2020",
}


def baseline_icc_name(gamut: Gamut) -> str:
    return _BASELINE_NAME[gamut]


def resolve_icc_bytes(kind: IccKind, gamut: Gamut, curve: TransferCurve) -> bytes | None:
    """按种类取 ICC 字节（运行时读资产或生成器缓存）。"""
    if kind == IccKind.NONE:
        return None
    if kind == IccKind.BASELINE:
        return get_baseline_display_icc(gamut)
    if not should_embed_hdr_icc(gamut, curve):
        # HDR 生成器仅覆盖 PQ/HLG/Linear；其它曲线回退 baseline
        return get_baseline_display_icc(gamut)
    return get_hdr_icc(gamut, curve)


def resolve_icc_name(kind: IccKind, gamut: Gamut, curve: TransferCurve) -> str | None:
    if kind == IccKind.NONE:
        return None
    if kind == IccKind.BASELINE:
        return baseline_icc_name(gamut)
    if should_embed_hdr_icc(gamut, curve):
        return get_hdr_icc_name(gamut, curve)
    return baseline_icc_name(gamut)


def default_embed_icc(fmt: OutputFormat) -> bool:
    """格式默认是否嵌入 ICC（可被 EncodeOptions.embed_icc 覆盖）。"""
    if fmt in (OutputFormat.PNG, OutputFormat.JPG):
        return True
    # HEIF/AVIF/JXL：默认仅 NCLX/nclx。
    # HDR ICC（libjxl tone-map LUT）与 nclx 并存时，部分查看器优先走 ICC
    # 软打样，非 BT.2020（sRGB/P3 PQ）易偏红；与 HEIF/AVIF 策略对齐。
    return False


def plan_icc_embed(
    fmt: OutputFormat,
    gamut: Gamut,
    curve: TransferCurve,
    delivery: HdrDeliveryMode,
    *,
    embed_icc: bool | None = None,
) -> IccEmbedPlan:
    """决定本次输出应嵌入何种 ICC。"""
    do_embed = default_embed_icc(fmt) if embed_icc is None else bool(embed_icc)
    gain = uses_gainmap(delivery)

    if fmt == OutputFormat.PNG:
        if is_hdr_curve(curve):
            return IccEmbedPlan(
                kind=IccKind.HDR,
                embed=True,
                profile_name=get_hdr_icc_name(gamut, curve),
                windows_photos_safe=True,
                reason="PNG：HDR 曲线写 libjxl iCCP（SDR 查看器靠 LUT；HDR 靠 cICP）",
            )
        return IccEmbedPlan(
            kind=IccKind.BASELINE,
            embed=True,
            profile_name=baseline_icc_name(gamut),
            windows_photos_safe=True,
            reason="PNG：SDR 曲线写 Apple baseline iCCP，避免仅靠 cICP 时部分查看器偏色",
        )

    if fmt == OutputFormat.JPG:
        if gain:
            # 主图 baseline 由 gainmap 路径单独处理；此处描述「主图」策略
            return IccEmbedPlan(
                kind=IccKind.BASELINE,
                embed=True,
                profile_name=baseline_icc_name(gamut),
                windows_photos_safe=True,
                reason="Ultra HDR：主图 Apple baseline；副图另嵌 HDR ICC",
            )
        return IccEmbedPlan(
            kind=IccKind.BASELINE,
            embed=True,
            profile_name=baseline_icc_name(gamut),
            windows_photos_safe=True,
            reason="JPG Direct（sRGB 曲线）：嵌入 baseline ICC 标明色域",
        )

    if fmt == OutputFormat.JXL:
        if gain:
            return IccEmbedPlan(
                kind=IccKind.HDR if is_hdr_curve(curve) else IccKind.BASELINE,
                embed=do_embed,
                profile_name=get_hdr_icc_name(gamut, curve)
                if is_hdr_curve(curve)
                else baseline_icc_name(gamut),
                windows_photos_safe=not do_embed,
                reason=(
                    "JXL Gain Map：默认仅码流/nclx；"
                    "embed_icc=True 时 jhgm alt_icc 写 HDR ICC（部分查看器可能偏色）"
                ),
            )
        if is_hdr_curve(curve):
            return IccEmbedPlan(
                kind=IccKind.HDR,
                embed=do_embed,
                profile_name=get_hdr_icc_name(gamut, curve),
                windows_photos_safe=not do_embed,
                reason=(
                    "JXL Direct HDR：默认仅 nclx + 码流 ColourEncoding；"
                    "embed_icc=True 才加 colr/prof（tone-map LUT，易与 HDR 显示冲突偏红）"
                ),
            )
        return IccEmbedPlan(
            kind=IccKind.BASELINE,
            embed=do_embed,
            profile_name=baseline_icc_name(gamut),
            windows_photos_safe=True,
            reason="JXL SDR：默认仅 nclx；可选 baseline prof",
        )

    if fmt in (OutputFormat.AVIF, OutputFormat.HEIF):
        kind = IccKind.HDR if is_hdr_curve(curve) and not gain else IccKind.BASELINE
        name = (
            get_hdr_icc_name(gamut, curve)
            if kind == IccKind.HDR and is_hdr_curve(curve)
            else baseline_icc_name(gamut)
        )
        if not do_embed:
            return IccEmbedPlan(
                kind=IccKind.NONE,
                embed=False,
                profile_name=None,
                windows_photos_safe=True,
                reason=(
                    f"{fmt.value.upper()}：默认仅 NCLX（Windows 照片 + ICC 易全红）；"
                    "需要时设 embed_icc=True"
                ),
            )
        return IccEmbedPlan(
            kind=kind if is_hdr_curve(curve) and not gain else IccKind.BASELINE,
            embed=True,
            profile_name=name,
            windows_photos_safe=False,
            reason=(
                f"{fmt.value.upper()}：显式嵌入 ICC（colr/prof）；"
                "Windows 照片可能异常，请用其它查看器验证"
            ),
        )

    return IccEmbedPlan(
        kind=IccKind.NONE,
        embed=False,
        profile_name=None,
        windows_photos_safe=True,
        reason=f"未定义的格式策略: {fmt}",
    )


def plan_and_bytes(
    fmt: OutputFormat,
    gamut: Gamut,
    curve: TransferCurve,
    delivery: HdrDeliveryMode,
    *,
    embed_icc: bool | None = None,
) -> tuple[IccEmbedPlan, bytes | None]:
    plan = plan_icc_embed(fmt, gamut, curve, delivery, embed_icc=embed_icc)
    if not plan.embed or plan.kind == IccKind.NONE:
        return plan, None
    return plan, resolve_icc_bytes(plan.kind, gamut, curve)
