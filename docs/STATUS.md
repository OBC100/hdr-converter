# 项目状态总览（2026-07）

本文档汇总**验收状态**、**已知限制**与**下一阶段**。产品已从「JXR→PNG」演进为多格式 HDR/SDR 互转；架构见 [PROJECT.md](PROJECT.md)。

---

## 1. 验收状态

**正式验收范围：传输曲线 = PQ。** HLG / Linear / sRGB Gamma 仅有实验性实现，**未做同等回归与目视验收**。输出容器不含 JXR（JXR 仅作输入）。

### 1.1 输出（PQ）

| 输出 | PQ Direct | PQ Gain Map | 备注 |
|------|:---------:|:-----------:|------|
| **PNG** | ✓ | — | [CHECKPOINT_PNG.md](CHECKPOINT_PNG.md)（PQ 路径） |
| **JPG (Ultra HDR)** | — | mono / color | [CHECKPOINT_JPEG_PQ.md](CHECKPOINT_JPEG_PQ.md) |
| **JXL** | ✓ | mono / color | ``jhgm``；imagecodecs/libjxl |
| **HEIF** | ✓ | mono / color | 需 pillow-heif |
| **AVIF** | ✓ | mono / color | imagecodecs + isobmff_gainmap |
| **JXR** | — | — | 不支持写出 |

### 1.2 输入（格式互通，PQ 主路径验证）

| 输入 | Direct | Gain Map demux | GUI |
|------|:------:|:--------------:|:---:|
| JXR | ✓ | — | ✓ |
| PNG | ✓（cICP / sRGB） | — | ✓ |
| JPEG | ✓ | Ultra HDR（APP2） | ✓ |
| AVIF | ✓ | ✓ | ✓ |
| HEIF | ✓（pillow-heif） | ✓ | ✓ |
| JXL | ✓ | ✓（jhgm） | ✓ |

### 1.3 其它曲线（实验性）

| 曲线 | 代码路径 | 验收 |
|------|----------|------|
| **HLG** | 已实现（PNG / JPG Ultra HDR / HEIF / AVIF / JXL） | **未正式测试**；早期笔记见 [CHECKPOINT_JPEG_HLG.md](CHECKPOINT_JPEG_HLG.md) |
| **Linear** | 已实现（PNG / HEIF / AVIF / JXL；JPG 不支持） | **未正式测试** |
| **sRGB Gamma** | SDR / 普通 JPEG 等 | 随容器默认行为，非 HDR 主验收项 |

**多格式进度**：Stage A–G、**D2** ✅。下一阶段：**H·H2**（元数据与 XMP hdrgm）— [EXECUTION_PLAN.md](EXECUTION_PLAN.md)。

---

## 2. 当前默认与推荐参数（JPG Ultra HDR，PQ）

| 参数 | 验收/推荐值 | 说明 |
|------|-------------|------|
| `curve` | **PQ**（正式验收） | HLG 实验性；JPG 不支持 Linear |
| `hdr_delivery` | `gainmap_mono` | `gainmap_color` 已实现 |
| `gainmap_scale` | 2（½） | Full / ¼ / ⅛ 可选 |
| `encode_level` | 90 | Q88 可再省 ~11% 体积 |
| `jpeg_subsampling` | 420（4:2:0） | 422 / 444 可选 |
| `sdr_tonemap` | **`hable_max`（默认）** | 另可选 `chrome` / `safari`，见 §3 |

JPEG 编码：`imagecodecs.mozjpeg`（`optimize` + `progressive` + trellis）。相对 Pillow @90，Forza 样张 Ultra HDR 约 **-21%** 体积。

---

## 3. SDR 色调映射

Gain Map SDR 基础图可选：

| 算子 | 说明 |
|------|------|
| **`HABLE_MAX`（默认）** | Uncharted 2 max-RGB；峰值 = 99.9% MaxCLL + 超亮面积自适应 cap（1000/2000/4000） |
| **`CHROME`** | Chromium 有理函数；`maxIn = peak/203`，peak 同上 |
| **`SAFARI`** | BT.2408 Annex 5 max-RGB；源峰值同上 → 203 nits |

GUI / CLI：`--sdr-tonemap hable_max|chrome|safari`。

---

## 4. 文档索引

| 文档 | 用途 |
|------|------|
| [PROJECT.md](PROJECT.md) | **产品定位与架构**（多格式输入输出、canonical、可配置参数） |
| [ICC_PROFILES.md](ICC_PROFILES.md) | **ICC 生成器与各格式嵌入策略**（查看器偏色/全红说明） |
| [STATUS.md](STATUS.md) | **本页** — 状态与路线图 |
| [CHECKPOINT_PNG.md](CHECKPOINT_PNG.md) | PNG 定稿（正式验收以 PQ 为准） |
| [CHECKPOINT_PQ.md](CHECKPOINT_PQ.md) | PQ ICC / PNG 实现历程 |
| [CHECKPOINT_JPEG_PQ.md](CHECKPOINT_JPEG_PQ.md) | Ultra HDR JPEG（PQ）定稿 |
| [CHECKPOINT_JPEG_HLG.md](CHECKPOINT_JPEG_HLG.md) | Ultra HDR JPEG（HLG）— 实验性参考 |
| [UNIFIED_PIPELINE.md](UNIFIED_PIPELINE.md) | 统一导出管线（L0/L1/L2） |
| [MULTI_FORMAT_PLAN.md](MULTI_FORMAT_PLAN.md) | 全格式扩展方案 |
| [EXECUTION_PLAN.md](EXECUTION_PLAN.md) | 分阶段任务与验收 |

**脚本（测试 / 对比）**

| 脚本 | 用途 |
|------|------|
| `scripts/_gen_uhdr_all_gamuts.py` | 三色域 PQ/HLG UHDR |
| `scripts/_gen_baseline_only.py` | 仅 baseline 对照 |
| `scripts/_compare_mozjpeg.py` | Pillow vs mozjpeg 体积 |
| `scripts/_sweep_mozjpeg.py` | mozjpeg 参数扫描 |
| `scripts/_compare_uhdr.py` | 原生 vs libultrahdr 结构 |
| `scripts/_benchmark_jpg_export.py` | JPG 导出分阶段测速 + 优化对比 |
| `scripts/_profile_jpg_export.py` | JPG 导出 cProfile 剖析 |
| `scripts/_regress_stage_a.py` | Stage A：canonical 归一化 vs 旧 scRGB 路径回归 |
| `scripts/_regress_stage_b.py` | Stage B：CICP 反查 + PNG/JPEG 解码往返 |
| `scripts/_regress_stage_c.py` | Stage C：HEIF/AVIF/JXL Direct 解码往返 |
| `scripts/_regress_stage_d.py` | Stage D：`apply_gainmap` + Gain Map demux 往返 |
| `scripts/_regress_stage_e.py` | Stage E：格式检测 + `load_source_raw` + 直通 |
| `scripts/_regress_stage_g.py` | Stage G：JXR HDR 预览 vs 旧透传 |

---

## 5. JPG 导出性能（Forza 3840×2160，PQ Gain Map mono，Hable max，Q90）

同机样张、三轮平均（`scripts/_benchmark_jpg_export.py`）。输出像素一致（1228.9 KB，`ultrahdr_check` 通过）。

| 阶段 | 最初 | 当前 | 相对最初 |
|------|------|------|----------|
| UHDR 编码 | ~8000 ms | **~2185 ms** | **≈ 3.7×** |
| `convert_file`（含解码） | ~8370 ms | **~2387 ms** | **≈ 3.5×** |
| `convert_file`（**DecodeCache 命中**） | — | **~1992 ms** | 再省 ~395 ms 解码 |

优化轮次：① 合并管线（去重复色域/tone map）→ ② 3×3 矩阵一次转换 → ③ CPU 并行（CLL∥tone map、gain∥SDR、双路 mozjpeg）。

### 5.1 源像素缓冲（L0，`DecodeCache`）

预览与导出共用会话级 **scRGB 桥接缓冲**（`DecodeCache` / `load_source_raw`）。预览计算走 **canonical BT.2020**（Stage G）。详见 [PROJECT.md §4 / §9](PROJECT.md)。

| 时机 | 行为 |
|------|------|
| `PreviewWorker` | `load_jxr_raw` → 解码并 `cache.put` |
| 用户点转换 | `wait_preview_decode()` → `convert_batch(..., decode_cache=)` |
| 缓存命中 | `convert_file` 跳过 `decode_jxr`（GUI 典型路径） |
| 文件列表清空 / 变更 | `cache.clear()` / `drop_missing()` |

CLI / 脚本不传 `decode_cache` 时行为与引入缓存前一致。

---

## 6. 已知限制（简表）

- **正式验收仅 PQ**；HLG / Linear 等为实验性，结果可能与阅读器/期望不一致
- 输出不含 JXR（仅输入）
- JPG + PQ/HLG 强制 Gain Map；sRGB 曲线为普通 8-bit JPEG
- HEIF/AVIF Gain Map：手动 ISOBMFF（不依赖 libultrahdr）
- 预览：Win32 可选 D3D scRGB；不走 curve 编码；非 HDR 不启 D3D
- MaxCLL 动态（99.99% 分位），不可手动覆盖
- 包名 `hdr-converter`（模块 `hdr_converter`）；GUI 标题为「HDR 格式转换器」
- Ultra HDR 的 XMP `hdrgm:` 尚未做（H2）；Stage H 元数据袋骨架已建（`metadata_bag.py`）
- **ICC 写出**：PNG/JPG 默认嵌入；HEIF/AVIF/**JXL** 默认仅 NCLX/nclx（`--embed-icc` / GUI「嵌入 ICC」可选）。JXL 仍支持 sRGB/P3/BT.2020
- **Windows JPEG XL HDR 解码器 bug（非本工具问题，不可修）**：Windows「JPEG XL Image Extension」对 HDR（float）JXL 一律按 BT.2020 解码，P3/sRGB 输出在 Windows 照片上会偏红/过饱和，浏览器（原生 libjxl）不受影响；写不写 ICC 均无效。已向 Microsoft 确认为已知缺陷，见 [microsoft/WindowsAppSDK#5390](https://github.com/microsoft/WindowsAppSDK/issues/5390) 与 [ICC_PROFILES.md §4.6](ICC_PROFILES.md)
- **ICC 读入（D2）**：PNG iCCP / JPEG `icc_profile` → 命名空间或匿名原色；CICP cp=11 = DCI-P3
