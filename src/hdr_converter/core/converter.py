"""转换编排：JXR 加载（L0）→ 色彩转换 / Gain Map（L1）→ 目标格式编码。"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .cicp import Gamut, TransferCurve, get_cicp
from .color_pipeline import PipelineResult, convert_colorspace
from .decode_cache import DecodeCache, load_source_raw
from .encoders import get_encoder
from .encoders.base import EncodeOptions, OutputFormat, apply_encode_level
from .gainmap_pipeline import encode_gainmap
from .hdr_options import (
    DEFAULT_BASE_BITS,
    DEFAULT_GAINMAP_BITS,
    DEFAULT_GAINMAP_SCALE,
    DEFAULT_RTX_CONTRAST,
    DEFAULT_RTX_MAX_LUMINANCE,
    DEFAULT_RTX_MIDDLE_GRAY,
    DEFAULT_RTX_SATURATION,
    DEFAULT_RTX_VSR_SCALE,
    HdrDeliveryMode,
    RtxEnhanceMode,
    RtxVsrQuality,
    SdrToneMap,
    normalize_container_bits,
    normalize_gainmap_scale,
    normalize_jxl_bits,
    resolve_hdr_delivery,
    uses_gainmap,
)
from .jpeg_encode import DEFAULT_JPEG_SUBSAMPLING, JpegSubsampling, normalize_jpeg_subsampling
from .parallel import batch_workers, limit_blas_threads_in_child


@dataclass
class ConvertSettings:
    output_format: OutputFormat = OutputFormat.PNG
    # 与 EncodeOptions / CLI --gamut 默认值及 docs/PROJECT.md §8.1 保持一致
    gamut: Gamut = Gamut.BT2020
    curve: TransferCurve = TransferCurve.PQ
    quantize_bits: int | None = None  # Direct：8/10/12/14/16；HEIF/AVIF/JXL 映射 base_bits
    encode_level: int | None = None  # PNG: 0=无 oxipng, 1-6=等级；其它: 1-100 质量
    hdr_delivery: HdrDeliveryMode = HdrDeliveryMode.DIRECT
    base_bits: int = DEFAULT_BASE_BITS
    gainmap_bits: int = DEFAULT_GAINMAP_BITS
    gainmap_scale: int = DEFAULT_GAINMAP_SCALE
    sdr_tonemap: SdrToneMap | None = None
    jpeg_subsampling: JpegSubsampling = DEFAULT_JPEG_SUBSAMPLING
    # None=按格式默认（PNG/JPG/JXL 开；HEIF/AVIF 关）；见 icc_policy
    embed_icc: bool | None = None
    # NVIDIA RTX Video（TrueHDR / VSR）；需 hdr_rtx_bridge.dll
    rtx_enhance: RtxEnhanceMode = RtxEnhanceMode.OFF
    rtx_contrast: int = DEFAULT_RTX_CONTRAST
    rtx_saturation: int = DEFAULT_RTX_SATURATION
    rtx_middle_gray: int = DEFAULT_RTX_MIDDLE_GRAY
    rtx_max_luminance: int = DEFAULT_RTX_MAX_LUMINANCE
    rtx_vsr_quality: RtxVsrQuality = RtxVsrQuality.HIGH
    rtx_vsr_scale: int = DEFAULT_RTX_VSR_SCALE

    def to_encode_options(
        self,
        *,
        hdr_delivery: HdrDeliveryMode,
        base_bits: int,
        gainmap_bits: int = DEFAULT_GAINMAP_BITS,
        gainmap_scale: int | None = None,
        jpeg_subsampling: JpegSubsampling | None = None,
    ) -> EncodeOptions:
        """映射到编码器选项（已解析的 delivery / 位深 / 抽样）。"""
        return EncodeOptions(
            gamut=self.gamut,
            curve=self.curve,
            content_light=None,
            output_format=self.output_format,
            hdr_delivery=hdr_delivery,
            base_bits=base_bits,
            gainmap_bits=gainmap_bits,
            gainmap_scale=(
                gainmap_scale
                if gainmap_scale is not None
                else normalize_gainmap_scale(self.gainmap_scale)
            ),
            sdr_tonemap=self.sdr_tonemap,
            jpeg_subsampling=(
                jpeg_subsampling
                if jpeg_subsampling is not None
                else normalize_jpeg_subsampling(self.jpeg_subsampling)
            ),
            embed_icc=self.embed_icc,
        )


@dataclass
class ConvertResult:
    input_path: Path
    output_path: Path
    width: int
    height: int
    settings: ConvertSettings
    max_cll: int | None = None
    max_fall: int | None = None
    quantize_bits: int = 16


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    settings: ConvertSettings | None = None,
    *,
    raw: np.ndarray | None = None,
    decode_cache: DecodeCache | None = None,
) -> ConvertResult:
    settings = settings or ConvertSettings()
    input_path = Path(input_path)
    output_path = Path(output_path)

    # Stage E：同格式 Direct 且色彩参数一致 → 字节直通（RTX 增强时禁用）
    if raw is None and settings.rtx_enhance == RtxEnhanceMode.OFF:
        from .passthrough import try_passthrough

        if try_passthrough(input_path, output_path, settings):
            w, h = 0, 0
            try:
                from PIL import Image

                with Image.open(output_path) as im:
                    w, h = im.size
            except Exception:
                probed = None
                try:
                    from .passthrough import _probe_source_params
                    from .format_detect import detect_format

                    probed = _probe_source_params(input_path, detect_format(input_path))
                except Exception:
                    pass
                if probed is None:
                    # 尺寸未知时仍完成直通；下游很少依赖 ConvertResult 宽高做像素运算
                    pass
            return ConvertResult(
                input_path=input_path,
                output_path=output_path,
                width=w,
                height=h,
                settings=settings,
                quantize_bits=settings.quantize_bits or settings.base_bits,
            )

    raw = load_source_raw(input_path, cache=decode_cache, raw=raw)
    if settings.rtx_enhance != RtxEnhanceMode.OFF:
        from .rtx_video import apply_rtx_enhance

        # RTX 增强不走直通；结果尺寸可能因 VSR 变化，且勿写入未增强缓存键
        raw = apply_rtx_enhance(
            raw,
            settings.rtx_enhance,
            contrast=settings.rtx_contrast,
            saturation=settings.rtx_saturation,
            middle_gray=settings.rtx_middle_gray,
            max_luminance=settings.rtx_max_luminance,
            vsr_quality=settings.rtx_vsr_quality,
            vsr_scale=settings.rtx_vsr_scale,
        )
    delivery = resolve_hdr_delivery(
        settings.output_format,
        settings.curve,
        settings.hdr_delivery,
    )
    cicp = get_cicp(settings.gamut, settings.curve)

    base_bits = settings.base_bits
    container_direct = settings.output_format in (
        OutputFormat.HEIF,
        OutputFormat.AVIF,
        OutputFormat.JXL,
    )
    if settings.quantize_bits is not None and container_direct:
        base_bits = settings.quantize_bits
    if settings.output_format in (OutputFormat.HEIF, OutputFormat.AVIF):
        base_bits = normalize_container_bits(base_bits)
    elif settings.output_format == OutputFormat.JXL:
        base_bits = normalize_jxl_bits(base_bits)
    # 增益图固定 8-bit（与 JPG Ultra HDR 一致）；忽略传入的 gainmap_bits
    gainmap_bits = DEFAULT_GAINMAP_BITS
    gainmap_scale = normalize_gainmap_scale(settings.gainmap_scale)
    jpeg_subsampling = normalize_jpeg_subsampling(settings.jpeg_subsampling)

    pipeline_quantize = settings.quantize_bits
    if settings.output_format in (OutputFormat.HEIF, OutputFormat.AVIF) and uses_gainmap(delivery):
        pipeline_quantize = None
    elif container_direct:
        pipeline_quantize = base_bits

    encode_opts = settings.to_encode_options(
        hdr_delivery=delivery,
        base_bits=base_bits,
        gainmap_bits=gainmap_bits,
        gainmap_scale=gainmap_scale,
        jpeg_subsampling=jpeg_subsampling,
    )
    apply_encode_level(encode_opts, settings.output_format, settings.encode_level)

    if uses_gainmap(delivery):
        cll = encode_gainmap(
            raw,
            output_path,
            encode_opts,
            cicp,
        )
        effective_bits = 8 if settings.output_format == OutputFormat.JPG else base_bits
        pipeline = PipelineResult(
            rgb=raw,
            content_light=cll,
            effective_bits=effective_bits,
            is_uint16=False,
        )
    else:
        pipeline: PipelineResult = convert_colorspace(
            raw,
            settings.gamut,
            settings.curve,
            quantize_bits=pipeline_quantize,
        )
        encode_opts.bit_depth = pipeline.effective_bits
        encode_opts.content_light = pipeline.content_light
        encoder = get_encoder(settings.output_format)
        encoder.encode(pipeline, output_path, encode_opts, cicp)

    h, w = raw.shape[:2]
    cll = pipeline.content_light
    return ConvertResult(
        input_path=input_path,
        output_path=output_path,
        width=w,
        height=h,
        settings=settings,
        max_cll=cll.max_cll if cll else None,
        max_fall=cll.max_fall if cll else None,
        quantize_bits=pipeline.effective_bits,
    )


def _convert_file_task(
    args: tuple[Path, Path, ConvertSettings, np.ndarray | None],
) -> ConvertResult:
    limit_blas_threads_in_child()
    src, dst, settings, raw = args
    return convert_file(src, dst, settings, raw=raw)


def convert_batch(
    files: list[Path],
    output_dir: Path | None,
    settings: ConvertSettings,
    *,
    on_progress: callable | None = None,
    jobs: int | None = None,
    decode_cache: DecodeCache | None = None,
) -> list[ConvertResult]:
    ext = settings.output_format.value
    if not files:
        return []

    out_root = output_dir
    tasks: list[tuple[Path, Path, ConvertSettings, np.ndarray | None]] = []
    for src in files:
        out = out_root if out_root is not None else src.parent
        out.mkdir(parents=True, exist_ok=True)
        dst = out / f"{src.stem}.{ext}"
        cached_raw = decode_cache.get(src) if decode_cache is not None else None
        tasks.append((src, dst, settings, cached_raw))

    worker_count = jobs if jobs is not None else batch_workers(len(files))
    has_cached_raw = any(task[3] is not None for task in tasks)
    if has_cached_raw and worker_count > 1:
        worker_count = 1
    if worker_count <= 1:
        results: list[ConvertResult] = []
        for i, (src, dst, cfg, cached_raw) in enumerate(tasks):
            results.append(
                convert_file(
                    src,
                    dst,
                    cfg,
                    raw=cached_raw,
                    decode_cache=decode_cache,
                )
            )
            if on_progress:
                on_progress(i + 1, len(files), src.name)
        return results

    results_by_src: dict[Path, ConvertResult] = {}
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(_convert_file_task, task): task[0] for task in tasks
        }
        done = 0
        for fut in as_completed(futures):
            src = futures[fut]
            results_by_src[src] = fut.result()
            done += 1
            if on_progress:
                on_progress(done, len(files), src.name)

    return [results_by_src[src] for src, _, _, _ in tasks]
