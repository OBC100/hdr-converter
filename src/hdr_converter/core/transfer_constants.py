"""ST 2084 PQ / HLG 共享常量（encode 与 decode 必须一致）。"""

from __future__ import annotations

PQ_PEAK_NITS = 10_000.0
HLG_REF_DISPLAY_NITS = 1000.0

# SMPTE ST 2084（与 libjxl / ITU 标准分数形式）
PQ_M1 = 1305 / 8192
PQ_M2 = 2523 / 32
PQ_C1 = 107 / 128
PQ_C2 = 2413 / 128
PQ_C3 = 2392 / 128

# John Hable / Uncharted 2 filmic（预览与 Gain Map SDR 共用系数）
HABLE_A = 0.15
HABLE_B = 0.50
HABLE_C = 0.10
HABLE_D = 0.20
HABLE_E = 0.02
HABLE_F = 0.30
HABLE_WHITE = 11.2
HABLE_E_OVER_F = HABLE_E / HABLE_F
