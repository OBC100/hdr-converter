# -*- coding: utf-8 -*-
"""诊断：增益图编码→ISO 21496 解码往返，测量高光重建误差。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.color_pipeline import compute_content_light
from hdr_converter.core.gainmap_math import (
    _SDR_WHITE_NITS,
    compute_gainmap_with_peak,
)
from hdr_converter.core.sdr_tonemap import (
    apply_sdr_tonemap_linear,
    compute_tonemap_peak_nits,
    gamut_map_sdr_linear,
)
from hdr_converter.core.hdr_options import SdrToneMap

PQ_PEAK = 10000.0


def iso_decode(sdr_linear, gain_u8, meta):
    """ISO 21496-1 / libultrahdr applyGain（w=1，全增益，忽略上采样）。"""
    g = gain_u8.astype(np.float64) / 255.0
    gamma = meta.gamma[0]
    if gamma != 1.0:
        g = g ** (1.0 / gamma)
    log_min = np.log2(meta.min_content_boost[0])
    log_max = np.log2(meta.max_content_boost[0])
    factor = np.exp2(log_min * (1.0 - g) + log_max * g)
    sdr = sdr_linear.astype(np.float64)  # 0-1，SDR 白=1
    hdr = (sdr + meta.offset_sdr[0]) * factor[..., None] - meta.offset_hdr[0]
    return hdr * _SDR_WHITE_NITS  # nits


def run_case(name, nits_img):
    # nits → scRGB 目标色域绝对线性（1.0 = 80 nits）
    linear_abs = (nits_img / 80.0).astype(np.float32)
    peak_tm = compute_tonemap_peak_nits(linear_abs)
    sdr_linear = gamut_map_sdr_linear(
        apply_sdr_tonemap_linear(linear_abs, SdrToneMap.HABLE_MAX, peak_nits=peak_tm)
    )
    hdr_linear = (linear_abs / 125.0).astype(np.float32)  # 1.0 = 10000 nits
    cll = compute_content_light(hdr_linear)
    gain, meta = compute_gainmap_with_peak(
        hdr_linear, sdr_linear, Gamut.BT2020, TransferCurve.PQ,
        float(cll.max_cll), scale=1, multichannel=False,
    )
    gain3 = np.repeat(gain[..., None], 1, axis=-1) if gain.ndim == 2 else gain
    recon = iso_decode(sdr_linear, gain, meta)

    orig = nits_img.max(axis=-1)
    reco = recon.max(axis=-1)
    print(f"\n=== {name} ===")
    print(f"tone map peak      : {peak_tm:.0f} nits")
    print(f"MaxCLL (99.99%)    : {cll.max_cll} nits -> max_boost = {meta.max_content_boost[0]:.2f}")
    print(f"真实峰值           : {orig.max():.0f} nits")
    print(f"重建峰值           : {reco.max():.0f} nits  (损失 {100*(1-reco.max()/orig.max()):.1f}%)")
    for target in (500, 1000, 2000, 4000, 8000, 10000):
        mask = np.isclose(orig, target, rtol=0.02)
        if mask.any():
            r = float(np.median(reco[mask]))
            print(f"  {target:>6.0f} nits 像素 -> 重建 {r:>7.1f} nits ({100*r/target:>5.1f}%)")


def main():
    rng = np.random.default_rng(42)
    h, w = 512, 512
    n = h * w

    # 场景A：游戏典型帧——大部分中间调，0.005% 太阳级镜面高光 10000 nits
    base = rng.uniform(5, 150, size=(h, w, 1)).astype(np.float64)
    img = np.repeat(base, 3, axis=-1)
    # 阶梯高光块
    for i, v in enumerate((500, 1000, 2000, 4000, 8000)):
        img[10 + i * 6: 14 + i * 6, 10:14, :] = v
    # 极小太阳
    img[300:302, 300:307, :] = 10000.0  # 14 px = 0.005%
    run_case("A: 中间调 + 0.005% 太阳(10000 nits)", img)

    # 场景B：2.5% 大面积高光 5000 nits（触发 4000 cap）
    img2 = np.repeat(rng.uniform(5, 300, size=(h, w, 1)), 3, axis=-1)
    img2[:82, :, :] = 5000.0
    img2[300:302, 300:307, :] = 10000.0
    run_case("B: 大面积 5000 nits 高光 + 太阳", img2)


if __name__ == "__main__":
    main()
