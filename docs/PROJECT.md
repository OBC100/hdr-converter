# HDR 格式转换器 — 项目文档

> 包名 / CLI：`hdr-converter`（模块 `hdr_converter`）。产品定位已从「仅 JXR→PNG」演进为 **HDR/SDR 多格式互转**。

## 实现状态（2026-07）

> 验收总览与路线图见 **[STATUS.md](STATUS.md)**。架构扩展见 **[MULTI_FORMAT_PLAN.md](MULTI_FORMAT_PLAN.md)** / **[EXECUTION_PLAN.md](EXECUTION_PLAN.md)**。

| 方向 | 状态 |
|------|------|
| **输出** PNG / JPG Ultra HDR / HEIF / AVIF / JXL（**无 JXR 写出**） | ✅ **PQ** 已验收（Direct + Gain Map） |
| **输入** JXR / PNG / JPEG / HEIF / AVIF / JXL | ✅ Stage A–E；GUI 已接通（PQ 主路径） |
| **预览** canonical BT.2020 → scRGB 呈现 | ✅ Stage G |
| **其它曲线** HLG / Linear / sRGB | ⚠ 实验性实现，**未正式测试** |
| **待做** 统一元数据（H/H2） | 见执行计划；D2 任意 ICC 已完成 |

**SDR 色调映射（Gain Map）**：默认 `hable_max`，可选 `chrome` / `safari`。

---

## 1. 项目概述

### 1.1 产品定位（相对早期目标的变化）

| | 早期（起点） | 当前（2026-07） |
|--|-------------|----------------|
| 输入 | 仅 Windows HDR 截图 `.jxr` | JXR + PNG/JPEG/HEIF/AVIF/JXL（含 Gain Map demux） |
| 输出 | 以 PNG 为主 | PNG / Ultra HDR JPG / HEIF / AVIF / JXL（**不含 JXR**） |
| 中间表示 | 写死 scRGB（1.0≈80 nits） | `SourceImage` → **canonical BT.2020**（1.0=10000 nits）→ 现有 L1 经 scRGB 桥接 |
| GUI | 选 JXR、看预览、导出 | 多格式打开/拖放；非 HDR 不启 D3D；HDR 预览走统一 canonical 路径 |

仍保留：Fluent GUI、CLI、ISO 21496 Gain Map（自研 mux/demux，不依赖 libultrahdr）。

### 1.2 能力一览

| 项目 | 说明 |
|------|------|
| **输入** | `.jxr` / `.png` / `.jpg` / `.heif`·`.heic` / `.avif` / `.jxl`（魔数优先，见 `format_detect`） |
| **输出** | PNG / JPG / HEIF / AVIF / JXL（JXR 仅输入） |
| **色域** | sRGB、Display P3、BT.2020；任意 ICC 原色（ProPhoto / AdobeRGB / DCI-P3 等，Stage D2） |
| **曲线** | **PQ（正式验收）**；HLG / Linear / sRGB Gamma 为实验性 |
| **HDR 交付** | Direct（cICP/NCLX）或 Gain Map mono/color |
| **界面** | PyQt6 Fluent GUI + CLI |

### 1.3 输出格式能力

| 格式 | SDR | HDR PQ | HDR HLG※ | HDR Linear※ | Gain Map | 容器精度 |
|------|:---:|:------:|:--------:|:-----------:|:--------:|:--------:|
| PNG | ✓ | ✓ | ✓ | ✓ | ✗ | 16-bit IHDR |
| HEIF | ✓ | Direct | Direct | Direct | tmap mono/color | 8–12 bit |
| AVIF | ✓ | Direct | Direct | Direct | tmap mono/color | 8–12 bit |
| JPG | ✓ | — | — | — | Ultra HDR mono/color | 8-bit |
| JXL | ✓ | Direct / GM | Direct / GM | Direct | jhgm mono/color | 8–16 bit |

※ HLG / Linear：**代码可用，未正式验收**（见 [STATUS.md](STATUS.md) §1.3）。

色彩元数据见 **§7** 与 **[ICC_PROFILES.md](ICC_PROFILES.md)**；可配置项见 **§9**。


---

## 2. 技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| 语言 | Python 3.11+ | 主开发 |
| GUI | PyQt6 + PyQt-Fluent-Widgets | Fluent 界面；Win32 D3D11 HDR 预览 |
| 色彩 | colour-science + 自研矩阵 | 色域 / HLG / 预览 |
| 数组 | numpy | 像素 |
| 编解码 | imagecodecs、Pillow、pillow-heif、pyoxipng | JXR/PNG/AVIF/JXL/JPEG；HEIF 可选 |
| 打包 | PyInstaller | EXE |

国内安装优先清华 PyPI 镜像（仓库根目录 `pip.conf`）。

---

## 3. 项目结构（核心）

```
new project/
├── docs/
│   ├── PROJECT.md              # 本文件
│   ├── STATUS.md               # 验收与路线图入口
│   ├── MULTI_FORMAT_PLAN.md    # 多格式架构决策
│   ├── EXECUTION_PLAN.md       # 分阶段任务与验收
│   ├── UNIFIED_PIPELINE.md     # L0/L1/L2 导出分层
│   └── CHECKPOINT_*.md         # 各格式定稿纪要
├── scripts/
│   └── _regress_stage_{a..g}.py
└── src/hdr_converter/
    ├── cli.py / main.py
    ├── core/
    │   ├── source_image.py / canonical.py / decode_cache.py
    │   ├── format_detect.py / passthrough.py / decode_cache.py
    │   ├── decoders/           # png/jpeg/heif/avif/jxl → SourceImage
    │   ├── transfer_decode.py / cicp.py
    │   ├── color_pipeline.py / gainmap_*.py / isobmff_gainmap.py
    │   ├── converter.py / encoders/
    │   └── jxr_decoder.py
    └── gui/
        ├── preview_worker.py / preview_frame.py / preview_panel.py
        ├── hdr_d3d11.py / main_window.py
        └── i18n/locales/
```

---

## 4. 色彩与缓冲架构

### 4.1 分层总览

```
输入文件
  → L(-1) 解码器（按格式）→ SourceImage
       │  primaries + reference_white_nits + is_hdr
       │  （Gain Map 容器先 demux + apply_gainmap）
       ▼
  to_canonical_bt2020_linear()     # 唯一归一化：1.0 = 10000 nits，BT.2020
       │
       ├─ GUI 预览（Stage G）：canonical → 目标色域线性 → scRGB 呈现
       │
       └─ 导出（过渡期）：canonical → bt2020_linear_to_scrgb → L0 缓存
              → L1a Direct（convert_colorspace）或 L1b Gain Map（encode_gainmap）
              → L2 编码器 / 容器 mux
```

**为何还要 scRGB 桥接**：现有 Direct / Gain Map / 预览呈现层大量假设 Windows scRGB；Stage E 用桥接避免一次重写全部 L1。JXR 输入仍**直接缓存原生 scRGB**（零回归）。长期可将 L1 改为直接消费 canonical（见 MULTI_FORMAT_PLAN）。

### 4.2 亮度语义

| 表示 | 1.0 含义 | 典型来源 |
|------|----------|----------|
| Windows scRGB | ≈ 80 nits | JXR；`SCRGB_REFERENCE_WHITE_NITS` |
| SDR 显示线性 | ≈ 100 nits 图形白 | 无 HDR 元数据的 PNG/JPEG |
| **canonical BT.2020** | **10000 nits** | `to_canonical_bt2020_linear` |
| Gain Map / PQ 线性 | 10000 nits | Direct HDR、`apply_gainmap` 输出 |

JXR 缩放：`80/10000 = 1/125`，与历史 `_SCRGB_TO_HDR_LINEAR_SCALE` 一致。

### 4.3 解码与加载 API

| API | 作用 |
|-----|------|
| `detect_format(path)` | 魔数优先，扩展名兜底 |
| `decode_path_to_source_image(path)` | 分发到 `decoders/*` 或 JXR |
| `load_source_raw(path, cache=…)` | 产出 scRGB RGBA：JXR 原生；其它格式经桥接 |
| `load_jxr_raw` | `load_source_raw` 的别名（兼容旧调用） |
| `try_passthrough(…)` | 同格式 Direct 且色域/曲线一致 → 字节拷贝 |

缓存：`DecodeCache` 按路径 + mtime 存 scRGB，供预览与转换复用。

### 4.4 导出分支（L1）

| 交付 | 路径 |
|------|------|
| Direct | `raw` → `convert_colorspace` → `encoders/*`（cICP / NCLX / cLLi…） |
| Gain Map | `raw` → `encode_gainmap` → JPG MPF / HEIF·AVIF ISOBMFF / JXL jhgm |

### 4.5 Gain Map 读路径（输入）

`apply_gainmap`（ISO 21496-1 逆）+ 各容器 demux：

- JPG：`uhdr_jpeg_mux.demux_ultra_hdr_jpeg`（APP2 二进制；XMP `hdrgm:` → Stage H2）
- HEIF/AVIF：`extract_gainmap_items` + 单图重建解码
- JXL：`jhgm` 盒 + `parse_jhgm_bundle`

---

## 5. 编码与量化

### 5.1 `quantize_bits`（Direct）

PNG 容器固定 16-bit IHDR；有效精度 8/10/12/14/16，低位深左对齐。PQ/HLG 默认 10；Linear 默认 16。

### 5.2 `encode_level`

| 格式 | 含义 | 默认 |
|------|------|------|
| PNG | 0=关 oxipng；1–6=等级 | 2 |
| 有损格式 | 1–100 质量 | 90 |

### 5.3 HDR 交付（`HdrDeliveryMode`）

| 模式 | 适用 |
|------|------|
| Direct | HEIF / AVIF / JXL / PNG |
| Gain Map mono / color | HEIF / AVIF / JPG / JXL |

约束：Linear 禁止 Gain Map；JPG+PQ/HLG 强制 Gain Map。增益图固定 8-bit；`gainmap_scale` 默认 2（½ 分辨率）。

### 5.4 SDR tone map（仅 Gain Map）

`hable_max`（默认）/ `chrome` / `safari`。峰值策略与 MaxCLL 分位一致。容器侧：**不依赖 libultrahdr**，见 [UNIFIED_PIPELINE.md](UNIFIED_PIPELINE.md)。

### 5.5 JPG

mozjpeg（`optimize` + `progressive` + trellis）；`jpeg_subsampling` 默认 420。

---

## 6. 色彩元数据（编码侧）

PNG / JPG 默认嵌入匹配 ICC；HEIF/AVIF/**JXL** 默认仅 NCLX/nclx（可选 `--embed-icc`；JXL+HDR ICC 在非 BT.2020 上易偏红）。完整策略见 **[ICC_PROFILES.md](ICC_PROFILES.md)**。

### 6.1 cICP 速查

| Gamut | Curve | primaries | transfer | matrix |
|-------|-------|:---------:|:--------:|:------:|
| sRGB | sRGB/PQ/HLG/Linear | 1 | 13/16/18/8 | 0 |
| P3 | PQ/HLG/Linear | 12 | 16/18/8 | 0 |
| BT.2020 | PQ/HLG/Linear | 9 | 16/18/8 | 0（Linear 用 9） |

PNG：`IHDR → cICP → iCCP → [cLLi] → IDAT`。cLLi 仅 PQ/Linear。HEIF/AVIF Direct：NCLX + clli（通常无 ICC）。

反查表：`cicp_to_gamut_curve`（解码用）。

---

## 7. 历史说明（精简）

早期 hub / jxr 固定矩阵路径已删除，定稿为 **direct 直转**（`colour.RGB_to_RGB` / 自研矩阵）。与旧 hub 数值差约 1.5×10⁻⁵ 量级。详情见 git 历史与 [CHECKPOINT_PQ.md](CHECKPOINT_PQ.md)。

曾存在可选「亮度校准」（BT.2020 暗部 smoothstep）对齐部分 Windows/Chrome 观感；**已移除**（默认关且不利于跨设备一致性）。

已废弃的 HDR 预览尝试（QPainter scRGB、全局 scRGB + QPixmap 等）见早期笔记；现行方案为 **D3D11 FP16 scRGB**（`hdr_d3d11.py`）。

---

## 8. 可配置参数

### 8.1 用户可配（CLI / GUI / API）

| 参数 | 默认 | CLI | 说明 |
|------|------|-----|------|
| `output_format` | PNG | `--format` | 输出容器（无 jxr） |
| `gamut` | BT.2020 | `--gamut` | 目标色域 |
| `curve` | PQ | `--curve` | 传输曲线（正式验收以 PQ 为准） |
| `quantize_bits` | 曲线默认 | `--quantize-bits` | Direct 有效位深 |
| `hdr_delivery` | Direct | `--hdr-delivery` | direct / gainmap_mono / gainmap_color |
| `base_bits` | 10 | `--base-bits` | HEIF/AVIF/JXL |
| `gainmap_scale` | 2 | `--gainmap-scale` | 1/2/4/8 |
| `sdr_tonemap` | hable_max | `--sdr-tonemap` | Gain Map SDR |
| `encode_level` | 格式默认 | `--level` | PNG 0–6；其它 1–100 |
| `jpeg_subsampling` | 420 | `--jpeg-subsampling` | JPG |
| `embed_icc` | 按格式默认 | `--embed-icc` / `--no-embed-icc` | 见 [ICC_PROFILES.md](ICC_PROFILES.md) |

MaxCLL / MaxFALL、cICP/iCCP 由内容与 `gamut`+`curve` 自动决定，不可手动覆盖。

```python
from hdr_converter.core.converter import ConvertSettings, convert_file
from hdr_converter.core.encoders.base import OutputFormat
from hdr_converter.core.cicp import Gamut, TransferCurve

convert_file("in.avif", "out.png", ConvertSettings(
    output_format=OutputFormat.PNG,
    gamut=Gamut.P3,
    curve=TransferCurve.PQ,
    quantize_bits=10,
))
```

### 8.2 源码常量

| 位置 | 常量 | 值 |
|------|------|-----|
| `canonical.py` | `SCRGB_REFERENCE_WHITE_NITS` | 80 |
| | `SDR_REFERENCE_WHITE_NITS` | 100 |
| | `CANONICAL_PEAK_NITS` | 10000 |
| `color_pipeline.py` | `_SCRGB_TO_HDR_LINEAR_SCALE` | 1/125 |

---

## 9. GUI 预览（Stage G）

### 9.1 边界

预览核对 **目标色域**；不模拟 PQ/HLG 编码、不模拟 Gain Map tone map、不写元数据。Win32 可选 D3D scRGB 显示高光（>1.0）。

### 9.2 数据流

```
多格式输入
  → decode → SourceImage（或 JXR→原生 scRGB）
  → to_canonical_bt2020_linear
  → scale_preview_rgba（短边 ≤ 1080）
  → canonical_to_target_linear
  → SDR：Hable → ImageLabel
     HDR：linear_to_preview_scrgb → D3D11 FP16（is_hdr 门控）
```

同时写入会话缓存的 **scRGB 桥接缓冲**，供随后 `convert_batch(decode_cache=)` 免重复解码。

### 9.3 与旧行为

- 统一经 canonical（无「透传原生 scRGB」捷径）。
- JXR + BT.2020：与旧透传 p99.9 差约 2×10⁻⁴（色域矩阵往返残差，预览可接受）。
- 非 HDR 源：`need_hdr=False`，不启 D3D。

### 9.4 相关文件

| 文件 | 职责 |
|------|------|
| `preview_worker.py` | 解码、canonical、写缓存、`is_hdr` 门控 |
| `preview_frame.py` | canonical→SDR/HDR scRGB |
| `preview_panel.py` | 多格式拖放、L2 显示 |
| `hdr_d3d11.py` | D3D 呈现 |
| `passthrough.py` / `format_detect.py` | 直通与魔数 |

---

## 10. 参考与规范

| 资源 | 用途 |
|------|------|
| [jxr_to_png](https://github.com/ledoge/jxr_to_png) 等 | 早期 JXR/PQ 参考 |
| PNG 第三版 | cICP / cLLi |
| ISO 21496-1 | Gain Map |
| ISO/IEC 18181-2 | JXL jhgm |
| SMPTE ST 2084 / BT.2100 | PQ / HLG |

---

## 11. 文档索引

| 文档 | 用途 |
|------|------|
| [STATUS.md](STATUS.md) | 验收状态与下一步 |
| [MULTI_FORMAT_PLAN.md](MULTI_FORMAT_PLAN.md) | 为何采用 canonical 架构 |
| [EXECUTION_PLAN.md](EXECUTION_PLAN.md) | 阶段清单与验收 |
| [UNIFIED_PIPELINE.md](UNIFIED_PIPELINE.md) | 导出 L0/L1/L2 |
| [CHECKPOINT_*.md](.) | 单格式定稿 |
