"""跨格式的 HDR 色彩元数据（ICC + CICP/NCLX）。"""

from __future__ import annotations

from typing import Any

from .cicp import CICP, ContentLightLevel, Gamut, TransferCurve, get_cicp


def nclx_save_kwargs(cicp: CICP) -> dict[str, Any]:
    """HEIF/AVIF 等格式的 NCLX（等同 cICP）写入参数。

    同时提供扁平字段与 ``nclx_profile``：pillow-heif 需 ``set_nclx_profile``
    才会把 transfer/primaries 写入 HEVC VUI；仅靠 encode() 参数往往只写 colr 盒，
    部分阅读器（含 Windows）对 HLG 会回退读 VUI 而显示错误。
    """
    full = 1 if cicp.full_range else 0
    profile = {
        "color_primaries": cicp.color_primaries,
        "transfer_characteristics": cicp.transfer_characteristics,
        "matrix_coefficients": cicp.matrix_coefficients,
        "full_range_flag": full,
    }
    return {
        "save_nclx_profile": True,
        "nclx_profile": profile,
        **profile,
    }


def should_write_clli(curve: TransferCurve) -> bool:
    """cLLi/clli：PQ / Linear 写入；HLG 不写（对齐 PNG / PROJECT.md）。"""
    return curve in (TransferCurve.PQ, TransferCurve.LINEAR)


def avif_imagecodecs_kwargs(cicp: CICP) -> dict[str, int]:
    return {
        "primaries": cicp.color_primaries,
        "transfer": cicp.transfer_characteristics,
        "matrix": cicp.matrix_coefficients,
    }


def jpegxl_primaries(gamut: Gamut) -> int:
    """映射到 libjxl / imagecodecs ``JPEGXL.PRIMARIES``。

    Display P3 在 H.273 为 cp=12，但 libjxl 枚举仅有 ``P3=11``（基色同 P3、D65），
    与 cjxl / Chrome 对 Display P3 的惯用信号一致。
    """
    if gamut == Gamut.SRGB:
        return 1
    if gamut == Gamut.P3:
        return 11
    if gamut == Gamut.BT2020:
        return 9
    raise ValueError(f"不支持的色域: {gamut}")


def jpegxl_transfer(curve: TransferCurve) -> int:
    """映射到 libjxl ``JPEGXL.TRANSFER_FUNCTION``（与 H.273 / PNG cICP 同值）。"""
    return {
        TransferCurve.SRGB: 13,
        TransferCurve.LINEAR: 8,
        TransferCurve.PQ: 16,
        TransferCurve.HLG: 18,
    }[curve]


def jpegxl_encode_kwargs(
    gamut: Gamut,
    curve: TransferCurve,
    *,
    level: int,
    bitspersample: int,
    effort: int | None = None,
    usecontainer: bool = True,
) -> dict[str, Any]:
    """imagecodecs.jpegxl_encode 参数（ISO/IEC 18181 CICP 风格色彩信号）。"""
    from .hdr_options import DEFAULT_JXL_EFFORT

    return {
        "level": level,
        "effort": DEFAULT_JXL_EFFORT if effort is None else effort,
        "photometric": "rgb",
        "bitspersample": bitspersample,
        "primaries": jpegxl_primaries(gamut),
        "transfer": jpegxl_transfer(curve),
        "usecontainer": usecontainer,
    }


def avif_encode_kwargs(
    cicp: CICP | None = None,
    *,
    level: int,
    speed: int | None = None,
    numthreads: int | None = None,
    parallel_jobs: int = 1,
) -> dict[str, Any]:
    """imagecodecs.avif_encode 通用参数（含 speed / numthreads）。"""
    from .hdr_options import DEFAULT_AVIF_SPEED
    from .parallel import avif_encode_numthreads

    kwargs: dict[str, Any] = {
        "level": level,
        "speed": DEFAULT_AVIF_SPEED if speed is None else speed,
        "numthreads": (
            avif_encode_numthreads(parallel_jobs=parallel_jobs)
            if numthreads is None
            else numthreads
        ),
    }
    if cicp is not None:
        kwargs.update(avif_imagecodecs_kwargs(cicp))
    return kwargs


def get_baseline_cicp(gamut: Gamut) -> CICP:
    """SDR 基础图：目标基色 + sRGB 传递函数（ISO 21496 baseline colorimetry）。"""
    return get_cicp(gamut, TransferCurve.SRGB)


def get_baseline_cicp_for_isobmff(gamut: Gamut) -> CICP:
    """HEIF/AVIF Gain Map SDR 基础层 NCLX。

    cp/tc 同 baseline（目标基色 + sRGB）；matrix 固定 9（BT.2020 NCL）。
    不写 ICC ``colr``/``prof``（默认；Windows 照片对 HEIF/AVIF + ICC 常显示全红）。
    需要时设 ``ConvertSettings(embed_icc=True)``，见 ``docs/ICC_PROFILES.md``。
    """
    base = get_baseline_cicp(gamut)
    return CICP(
        base.color_primaries,
        base.transfer_characteristics,
        9,
        base.full_range,
    )


def get_direct_cicp_for_isobmff(gamut: Gamut, curve: TransferCurve) -> CICP:
    """HEIF/AVIF Direct HDR 的 NCLX。

    与 PNG cICP 同 primaries/transfer；matrix 在 identity(0) 时改为 9（BT.2020 NCL），
    与 Gain Map 基础层 / tmap 一致，避免部分阅读器把 identity RGB 解成通道错乱（全红等）。
    不嵌入 ICC ``colr``/``prof``（默认；Windows 照片对 HEIF/AVIF + HDR ICC 常显示异常）。
    需要时设 ``embed_icc=True``，见 ``docs/ICC_PROFILES.md``。
    """
    cicp = get_cicp(gamut, curve)
    if cicp.matrix_coefficients == 0:
        return CICP(
            cicp.color_primaries,
            cicp.transfer_characteristics,
            9,
            cicp.full_range,
        )
    return cicp


def get_direct_cicp_for_jxl(gamut: Gamut, curve: TransferCurve) -> CICP:
    """JPEG XL 容器级 ``colr``/nclx 的 NCLX（见 ``get_direct_cicp_for_isobmff``）。

    码流内部 ColourEncoding（libjxl 原生解码器读取，无 matrix 概念）已经正确；
    此处仅修正外挂 nclx 盒的 matrix_coefficients，避免部分阅读器（含 Windows
    照片）把 identity(0) + 宽色域 primaries 误处理成偏色（P3 常表现为偏红）。
    """
    return get_direct_cicp_for_isobmff(gamut, curve)


def get_gainmap_item_cicp() -> CICP:
    """增益图条目 NCLX：libavif 要求 cp=2、tc=2（未指定）；mc=6（BT.601）对齐参考样本。"""
    return CICP(2, 2, 6, full_range=True)


def ultrahdr_kwargs(
    curve: TransferCurve,
    content_light: ContentLightLevel | None = None,
    *,
    gamut: Gamut | None = None,
) -> dict[str, Any]:
    """Ultra HDR JPEG 色彩参数（脚本 / libultrahdr 对比用）。"""
    transfer = "PQ" if curve == TransferCurve.PQ else "HLG"
    gamut_name = {
        Gamut.SRGB: "BT_709",
        Gamut.P3: "DISPLAY_P3",
        Gamut.BT2020: "BT_2100",
    }.get(gamut or Gamut.BT2020, "BT_2100")
    kwargs: dict[str, Any] = {"gamut": gamut_name, "transfer": transfer}
    if content_light is not None:
        kwargs["nits"] = float(content_light.max_cll)
    return kwargs
