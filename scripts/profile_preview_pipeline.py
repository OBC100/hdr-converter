import time
import statistics as stats
from pathlib import Path

import numpy as np

from hdr_converter.core.decoders.jxr_decoder import decode_jxr, _require_jpegxr
from hdr_converter.core.cicp import Gamut
from hdr_converter.gui.preview_frame import (
    scale_preview_rgba,
    resize_rgba_bilinear,
    build_preview_frames_from_scrgb,
    build_sdr_preview_scrgb,
    build_hdr_preview_scrgb,
    scrgb_to_canonical_preview,
    scrgb_to_display_uint8,
    _PREVIEW_SHORT_EDGE,
)
from hdr_converter.core.color_pipeline import compute_content_light
from hdr_converter.core.decoders import jxr_decoder as jd
import inspect

REPEATS = 5


def bench(fn, *args, repeats=REPEATS, warmup=True):
    if warmup:
        fn(*args)
    times = []
    out = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn(*args)
        times.append((time.perf_counter() - t0) * 1000.0)
    sd = stats.stdev(times) if len(times) > 1 else 0.0
    return out, stats.mean(times), sd


def nearest_downsample_rgba(arr, short_edge=_PREVIEW_SHORT_EDGE):
    h, w = arr.shape[:2]
    short = min(h, w)
    target = max(1, int(short_edge))
    if short <= target:
        return arr
    nh = max(1, int(round(h * (target / short))))
    nw = max(1, int(round(w * (target / short))))
    step_y = max(1, int(round(h / nh)))
    step_x = max(1, int(round(w / nw)))
    return arr[::step_y, ::step_x, ...]


def main():
    candidates = [
        Path(r"c:\Users\OBC\Downloads\Horizon Forbidden West Complete Edition Screenshot 2026.02.16 - 18.31.55.12.jxr"),
        Path(r"c:\Users\OBC\source\repos\OBC100\test_output\test_input.jxr"),
    ]
    jxr_path = next((p for p in candidates if p.is_file()), None)

    imread, _ = _require_jpegxr()

    if jxr_path:
        source_note = "real JXR: " + str(jxr_path)
        scrgb, ms_dec, sd_dec = bench(decode_jxr, jxr_path)

        def decode_keep_f16(path):
            arr = np.asarray(imread(str(path)))
            if arr.dtype != np.float16:
                arr = arr.astype(np.float16)
            return arr

        f16_arr, ms_f16_decode, sd_f16 = bench(decode_keep_f16, jxr_path)
        native, ms_native, sd_native = bench(lambda p: np.asarray(imread(str(p))), jxr_path)
        native_dtype = native.dtype
    else:
        source_note = "synthetic float32 RGBA 3840x2160"
        scrgb = np.random.rand(2160, 3840, 4).astype(np.float32) * 0.8
        ms_dec = sd_dec = ms_f16_decode = sd_f16 = ms_native = sd_native = float("nan")
        native_dtype = None
        f16_arr = scrgb.astype(np.float16)

    _, ms_f16_cast, sd_f16c = bench(lambda a: a.astype(np.float32), f16_arr)

    h, w = scrgb.shape[:2]
    se = _PREVIEW_SHORT_EDGE / min(h, w)
    nw, nh = max(1, int(round(w * se))), max(1, int(round(h * se)))

    _, ms_scale, sd_scale = bench(scale_preview_rgba, scrgb)
    _, ms_resize, sd_resize = bench(resize_rgba_bilinear, scrgb, nw, nh)
    _, ms_nn, sd_nn = bench(nearest_downsample_rgba, scrgb)

    def stage_linear(s):
        can = scrgb_to_canonical_preview(scale_preview_rgba(s))
        return can

    small_b = scrgb_to_canonical_preview(scale_preview_rgba(scrgb))
    linear_b = small_b
    _, ms_b_scale, sd_b_scale = bench(scale_preview_rgba, scrgb)
    _, ms_b_linear, sd_b_linear = bench(stage_linear, scrgb)
    _, ms_b_cll, sd_b_cll = bench(compute_content_light, linear_b)
    cll = compute_content_light(linear_b)

    def stage_sdr():
        return build_sdr_preview_scrgb(
            small_b,
            gamut=Gamut.SRGB,
            linear=linear_b,
            max_fall_nits=cll.max_fall,
        )

    _, ms_b_sdr, sd_b_sdr = bench(stage_sdr)
    _, ms_b_hdr, sd_b_hdr = bench(
        lambda: build_hdr_preview_scrgb(
            small_b,
            gamut=Gamut.SRGB,
            linear=None,
        )
    )
    _, ms_full, sd_full = bench(
        lambda: build_preview_frames_from_scrgb(
            scrgb, gamut=Gamut.SRGB
        )
    )
    sdr_scrgb = stage_sdr()
    _, ms_uint8, sd_uint8 = bench(scrgb_to_display_uint8, sdr_scrgb)
    _, ms_scale_f16_path, sd_scale_f16 = bench(
        lambda a: scale_preview_rgba(a.astype(np.float32)), f16_arr
    )

    libs = {}
    for name, mod in [("scipy", "scipy"), ("cv2", "cv2"), ("PIL", "PIL")]:
        try:
            __import__(mod)
            libs[name] = "installed"
        except ImportError:
            libs[name] = "not installed"

    always_f32 = "astype(np.float32)" in inspect.getsource(jd.decode_jxr)

    def row(name, m, s):
        if m != m:
            print("{:<42} {:>10} {:>8}".format(name, "N/A", "N/A"))
        else:
            print("{:<42} {:>10.2f} {:>8.2f}".format(name, m, s))

    print("=" * 72)
    print("GUI preview pipeline profile")
    print(source_note)
    if jxr_path:
        print("Native imread dtype: {}".format(native_dtype))
    print("Input shape: {}, decode_jxr dtype: {}".format(scrgb.shape, scrgb.dtype))
    print("Preview ~ {}x{} (short edge {})".format(nw, nh, _PREVIEW_SHORT_EDGE))
    print("decode_jxr always astype(np.float32): {}".format(always_f32))
    print(
        "Optional resize backends: "
        + ", ".join("{}={}".format(k, v) for k, v in libs.items())
    )
    print("=" * 72)
    print("{:<42} {:>10} {:>8}".format("Stage", "mean ms", "stdev"))
    print("-" * 72)
    if jxr_path:
        row("imread native (no cast)", ms_native, sd_native)
        row("decode_jxr (astype float32)", ms_dec, sd_dec)
        row("decode keep float16 only", ms_f16_decode, sd_f16)
    row("float16 -> float32 astype", ms_f16_cast, sd_f16c)
    if jxr_path:
        row("scale after f16 (cast at resize)", ms_scale_f16_path, sd_scale_f16)
    row("scale_preview_rgba bilinear", ms_scale, sd_scale)
    row("resize_rgba_bilinear direct", ms_resize, sd_resize)
    row("nearest-neighbor downsample", ms_nn, sd_nn)
    print("-" * 72)
    row("build_preview: scale only", ms_b_scale, sd_b_scale)
    row("build_preview: linear convert", ms_b_linear, sd_b_linear)
    row("build_preview: compute_content_light", ms_b_cll, sd_b_cll)
    row("build_preview: build_sdr_preview_scrgb", ms_b_sdr, sd_b_sdr)
    row("build_preview: build_hdr_preview_scrgb", ms_b_hdr, sd_b_hdr)
    row("build_preview_frames (full)", ms_full, sd_full)
    row("scrgb_to_display_uint8 (1080p SDR)", ms_uint8, sd_uint8)
    print("=" * 72)
    if jxr_path:
        a = ms_f16_decode + ms_scale_f16_path
        b = ms_dec + ms_scale
        print(
            "Defer f32: f16_decode+scale {:.2f}ms vs decode_jxr+scale {:.2f}ms (delta {:+.2f}ms)".format(
                a, b, a - b
            )
        )


if __name__ == "__main__":
    main()