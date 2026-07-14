"""命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .core.cicp import Gamut, TransferCurve
from .core.color_pipeline import QUANTIZE_BITS_CHOICES
from .core.converter import ConvertSettings, convert_file
from .core.encoders.base import OutputFormat
from .core.hdr_options import (
    CONTAINER_BITS_CHOICES,
    GainMapScale,
    HdrDeliveryMode,
    RtxEnhanceMode,
    RtxVsrQuality,
    SdrToneMap,
)
from .core.jpeg_encode import normalize_jpeg_subsampling


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="多格式 HDR/SDR 转换：JXR/PNG/JPEG/HEIF/AVIF/JXL → PNG/HEIF/AVIF/JPG/JXL"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="输入文件（.jxr / .png / .jpg / .heif / .avif / .jxl 等）",
    )
    parser.add_argument("-o", "--output", type=Path, help="输出文件路径")
    parser.add_argument(
        "--format",
        choices=[e.value for e in OutputFormat],
        default=OutputFormat.PNG.value,
        help="输出格式",
    )
    parser.add_argument(
        "--gamut",
        choices=[e.value for e in Gamut],
        default=Gamut.BT2020.value,
        help="目标色域",
    )
    parser.add_argument(
        "--curve",
        choices=[e.value for e in TransferCurve],
        default=TransferCurve.PQ.value,
        help="传输曲线",
    )
    parser.add_argument(
        "--hdr-delivery",
        choices=[e.value for e in HdrDeliveryMode],
        default=HdrDeliveryMode.DIRECT.value,
        help="HDR 交付：Direct / Gain Map mono / Gain Map color",
    )
    parser.add_argument(
        "--base-bits",
        type=int,
        choices=list(CONTAINER_BITS_CHOICES),
        default=10,
        help="HEIF/AVIF 基础图位深；JXL 亦可用（8/10/12；14/16 请用 --quantize-bits）",
    )
    parser.add_argument(
        "--gainmap-scale",
        type=int,
        choices=[s.value for s in GainMapScale],
        default=GainMapScale.HALF.value,
        help="Gain Map 分辨率因子：1=Full, 2=1/2, 4=1/4, 8=1/8",
    )
    parser.add_argument(
        "--sdr-tonemap",
        choices=[e.value for e in SdrToneMap],
        default=None,
        help="Gain Map SDR tone map（默认 hable_max；chrome=Chromium 有理函数；safari=BT.2408 max-RGB）",
    )
    parser.add_argument(
        "--level",
        type=int,
        default=None,
        help="PNG: 0=无 oxipng, 1-6=压缩等级(默认2); HEIF/AVIF/JPG/JXL: 1-100 质量(默认90)",
    )
    parser.add_argument(
        "--quantize-bits",
        type=int,
        choices=list(QUANTIZE_BITS_CHOICES),
        default=None,
        help="PNG/JXL 有效量化位深；HEIF/AVIF 可兼作 base_bits",
    )
    parser.add_argument(
        "--jpeg-subsampling",
        choices=["420", "422", "444", "4:2:0", "4:2:2", "4:4:4"],
        default="420",
        help="JPG 色度抽样：4:2:0（默认）/ 4:2:2 / 4:4:4",
    )
    icc_group = parser.add_mutually_exclusive_group()
    icc_group.add_argument(
        "--embed-icc",
        dest="embed_icc",
        action="store_true",
        help="强制嵌入 ICC（HEIF/AVIF 默认不嵌；见 docs/ICC_PROFILES.md）",
    )
    icc_group.add_argument(
        "--no-embed-icc",
        dest="embed_icc",
        action="store_false",
        help="强制不嵌入 ICC（覆盖 PNG/JPG/JXL 默认）",
    )
    parser.add_argument(
        "--rtx-enhance",
        choices=[e.value for e in RtxEnhanceMode],
        default=RtxEnhanceMode.OFF.value,
        help="NVIDIA RTX Video：off / thdr / vsr / vsr_thdr（需 hdr_rtx_bridge.dll）",
    )
    parser.add_argument("--rtx-contrast", type=int, default=125)
    parser.add_argument("--rtx-saturation", type=int, default=100)
    parser.add_argument("--rtx-middle-gray", type=int, default=25)
    parser.add_argument("--rtx-max-luminance", type=int, default=1000)
    parser.add_argument(
        "--rtx-vsr-quality",
        choices=[e.value for e in RtxVsrQuality],
        default=RtxVsrQuality.HIGH.value,
    )
    parser.add_argument(
        "--rtx-vsr-scale",
        type=int,
        choices=[1, 2, 4],
        default=2,
        help="VSR 输出倍率",
    )
    parser.set_defaults(embed_icc=None)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    # 各枚举均为 str, Enum 子类，可直接按 .value 构造，无需手写映射表
    # （避免枚举新增取值时忘记同步这里，见 docs 中"统一管线"清理项）。
    output = args.output or args.input.with_suffix(f".{args.format}")
    settings = ConvertSettings(
        output_format=OutputFormat(args.format),
        gamut=Gamut(args.gamut),
        curve=TransferCurve(args.curve),
        encode_level=args.level,
        quantize_bits=args.quantize_bits,
        hdr_delivery=HdrDeliveryMode(args.hdr_delivery),
        base_bits=args.base_bits,
        gainmap_bits=8,
        gainmap_scale=args.gainmap_scale,
        sdr_tonemap=SdrToneMap(args.sdr_tonemap) if args.sdr_tonemap else None,
        jpeg_subsampling=normalize_jpeg_subsampling(args.jpeg_subsampling),
        embed_icc=args.embed_icc,
        rtx_enhance=RtxEnhanceMode(args.rtx_enhance),
        rtx_contrast=args.rtx_contrast,
        rtx_saturation=args.rtx_saturation,
        rtx_middle_gray=args.rtx_middle_gray,
        rtx_max_luminance=args.rtx_max_luminance,
        rtx_vsr_quality=RtxVsrQuality(args.rtx_vsr_quality),
        rtx_vsr_scale=args.rtx_vsr_scale,
    )
    result = convert_file(args.input, output, settings)
    msg = (
        f"Converted: {result.input_path.name} -> {result.output_path} "
        f"({result.width}x{result.height}) quantize={result.quantize_bits}bit"
    )
    if result.max_cll is not None:
        msg += f" MaxCLL={result.max_cll} MaxFALL={result.max_fall}"
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="backslashreplace").decode("ascii"))


if __name__ == "__main__":
    main()
