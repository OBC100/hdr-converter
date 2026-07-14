# 检查点：JPEG Ultra HDR — HLG（实验性）

> **状态更正（2026-07）**：当前项目正式验收仅覆盖 **PQ**。本文档保留 HLG 实现说明供参考；**HLG 未纳入正式回归与目视验收**。总览见 [STATUS.md](STATUS.md)。

PQ 定稿见 [CHECKPOINT_JPEG_PQ.md](CHECKPOINT_JPEG_PQ.md)。HLG 与 PQ **共用同一原生容器**（ISO 21496 + MPF + Apple baseline ICC），差异在副图 HDR ICC 与增益元数据语义。

---

## 1. 状态

| 模块 | 状态 |
|------|------|
| 原生 mux + `get_hdr_icc(gamut, HLG)` | **代码完成（实验性）** |
| 动态 MaxCLL（`resolve_hdr_peak_nits`） | **完成**（与 PQ 相同） |
| 三色域样张生成 | 有脚本；**未正式验收** |
| `ultrahdr_check` | 历史通过记录保留 |
| Windows 照片 / Chrome | **未作为正式验收项** |
| SDR 色调映射 | `HABLE_MAX`（与 PQ 相同） |

---

## 2. 与 PQ 的差异

| 项目 | PQ | HLG |
|------|----|-----|
| 副图 ICC | per-gamut PQ（cICP tf=16） | per-gamut HLG（cICP tf=18） |
| PNG Direct | `pq_encode` + cLLi | `hlg_encode_bt2100`，无 cLLi |
| Gain Map HDR 像素 | linear × 10000 nits | **相同** |
| `hdr_reference_nits` 回退 | 10000 | 1000（无内容统计时） |
| Baseline ICC | Apple 生成器，**与曲线无关** | 同左 |

Ultra HDR 的 alternate 意图在像素上为 **线性光**；HLG 由 ISO 元数据 + 副图 ICC 描述，副图 JPEG 不是 HLG OETF 编码值。

---

## 3. 默认编码参数

与 [CHECKPOINT_JPEG_PQ.md §2.1](CHECKPOINT_JPEG_PQ.md#21-默认编码参数) 相同，仅 `curve=HLG`、副图 ICC 为 HLG。

生成：

```bash
python scripts/_gen_uhdr_all_gamuts.py --curve hlg
```

---

## 4. 测试样本

| 文件 | ultrahdr_check |
|------|----------------|
| `Forza_Horizon_6_uhdr_hlg_srgb.jpg` | OK |
| `Forza_Horizon_6_uhdr_hlg_p3.jpg` | OK |
| `Forza_Horizon_6_uhdr_hlg_bt2020.jpg` | OK |
| `Forza_Horizon_6_uhdr_hlg.jpg` | P3 别名 |

输出目录：`scripts/_test_out/`

---

## 5. 验收清单

| 项 | 标准 | 状态 |
|----|------|------|
| 容器 | `ultrahdr_check` OK | ✓ |
| Baseline ICC | 548B Apple 生成器 | ✓ |
| 副图 ICC | `get_hdr_icc(gamut, HLG)`，tf=18 | ✓ |
| ISO 元数据 | 动态 `hdr_capacity_max` = MaxCLL/203 | ✓ |
| Windows 照片 | 不崩溃 | ✓ 初验 |
| 跨设备 HDR 显示 | Chrome / Android | 待办 |
| 回归 PQ | 三色域 PQ 样本不受影响 | ✓ |

---

## 6. 下一步

1. （已定稿）SDR tone map = `hable_max` — [STATUS.md §3](STATUS.md#3-sdr-色调映射)
2. Chrome / Android HDR 目视
3. 可选：native HLG vs `ultrahdr_encode(transfer=HLG)` 结构对比（`scripts/_compare_uhdr.py` 扩展）

---

## 7. 参考

| 来源 | 用途 |
|------|------|
| [CHECKPOINT_JPEG_PQ.md](CHECKPOINT_JPEG_PQ.md) | 容器、baseline、mozjpeg |
| [CHECKPOINT_PQ.md](CHECKPOINT_PQ.md) | ICC per-gamut |
| [PROJECT.md](PROJECT.md) §4.4、§6.4、§7.1 | HLG 管线与 cICP |
| ITU-R BT.2100 / BT.2408 | HLG OETF/OOTF |
