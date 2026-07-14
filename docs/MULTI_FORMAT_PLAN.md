# 全格式输入输出扩展方案（草案）

> 状态：**Stage A–G、D2 已完成**（2026-07）— 输出全格式已验收；多格式输入 + canonical 预览 + 任意 ICC 色域已落地。后续 H / H2。
> 相关文档：[PROJECT.md](PROJECT.md) §4.5、[UNIFIED_PIPELINE.md](UNIFIED_PIPELINE.md)、[STATUS.md](STATUS.md)、[EXECUTION_PLAN.md](EXECUTION_PLAN.md)。

---

## 1. 决策：两个极端方案都不选

| 方案 | 问题 |
|------|------|
| **A. 全部解码后转成 scRGB，复用现有 JXR 流水线** | 现有 `scrgb_to_gamut_linear_abs` 把源色域硬编码成 `sRGB`（见 §2），`_SCRGB_TO_HDR_LINEAR_SCALE=1/125` 把绝对亮度硬编码成 1.0=80nit。直接套用会把 BT.2020/P3 源色域先裁到 sRGB 再转出，产生色域裁切与精度损失；PQ/HLG 源的绝对亮度语义也会算错。 |
| **B. 每个格式各自独立实现** | PQ/HLG OETF、色域矩阵、MaxCLL 统计口径、3 种 SDR tone map 算子、ISO 21496 Gain Map 数学、ICC/cICP/NCLX 元数据规则要在 N 个格式里各写一遍；6 格式互转理论上 36 条通路，任何色彩科学修复要同步 N 处，必然出现不一致。 |

**采用方案 C（混合）**：解码器按格式独立（容器解析天然不同），但归一化到一个**格式无关的规范线性空间**后，完全复用现有 L1（色彩管线 + Gain Map 核心）/ L2（编码器）。复杂度从 N×M 降到 N（新解码器）+ M（已有编码器）。

---

## 2. 现状证据（为什么"直接套用 scRGB"是 bug 不是近似）

```72:78:src/hdr_converter/core/scrgb_colour.py
def scrgb_to_gamut_linear_abs(scrgb: np.ndarray, gamut: Gamut) -> np.ndarray:
    """
    scRGB → 目标色域显示线性（scRGB 绝对刻度，1.0 ≈ 80 nits）。
    经 XYZ 中转，保留 scRGB 负值所表达的扩展色域。
    """
    return _matmul_linear_rgb(scrgb, _matrix_scrgb_to_gamut(gamut))
```

`_matrix_scrgb_to_gamut` 内部把源色域写死为 `"sRGB"`；`color_pipeline.py` 的 `_SCRGB_TO_HDR_LINEAR_SCALE = 1/125` 把绝对亮度写死为 1.0=80nit。这两个假设只对 **Windows JXR 截图** 成立。

`gainmap_core.prepare_gainmap_linear` 同样直接调用 `scrgb_to_gamut_linear_abs`，继承了同样的假设。

现有 Gain Map 容器代码（`isobmff_gainmap.py::mux_gainmap_isobmff`、`uhdr_jpeg_mux.py::mux_ultra_hdr_jpeg`、`jxl_gainmap.py::mux_jxl_gainmap`）**只有写方向**，读方向（demux）缺失，是支持"其它格式作为输入"的必要缺口。`jxl_gainmap.py::parse_jhgm_bundle` 是个例外，已经有部分读方向实现，可参考其模式补齐其它容器。

---

## 3. 推荐架构：泛化 L(-1) 解码层，L1/L2 保持不变

```
┌───────────────────────────────────────────────────────────┐
│ L(-1) 解码适配器（新增，格式相关，每种输入一个模块）           │
│  JXR / PNG / JPEG(+UltraHDR) / HEIF(+gainmap) /              │
│  AVIF(+gainmap) / JXL(+jhgm)                                 │
│  职责：容器解析 + 色彩元数据读取 + 必要时 Gain Map 还原        │
│  产出：SourceImage（原生色域 + 原生线性 + 参考白 nits +        │
│         是否 HDR + 可选 ICC/EXIF passthrough）                │
└───────────────────────────┬───────────────────────────────┘
                            │ to_canonical_bt2020_linear()（新增，唯一一次归一化）
                            ▼
┌───────────────────────────────────────────────────────────┐
│ L0 规范缓冲（格式无关，取代写死的"scRGB"）                     │
│  float32 BT.2020 线性，绝对刻度 1.0 = 10000 nits              │
│  数值上 = 现有 scrgb_to_gamut_linear(scrgb, Gamut.BT2020) 的   │
│  结果；JXR 输入时 primaries=sRGB, ref_white=80，与现状零回归    │
└───────────────────────────┬───────────────────────────────┘
                            │ 完全复用现有代码，不改
         ┌──────────────────┴──────────────────┐
         ▼                                     ▼
   L1a Direct（color_pipeline.py）      L1b Gain Map（gainmap_core.py）
         │                                     │
         ▼                                     ▼
   L2 编码器（core/encoders/*）          L2 容器适配器（现有 mux_*）
```

### 3.1 统一中间结构 `SourceImage`

```python
@dataclass
class SourceImage:
    linear: np.ndarray            # float32 HxWx(3|4)，原生色域下显示线性，未裁剪
    primaries: Gamut               # 原生色域（NCLX/cICP/ICC 解析得出）
    reference_white_nits: float    # linear==1.0 对应绝对亮度
    is_hdr: bool
    alpha: np.ndarray | None
    embedded_gainmap: GainMapSource | None
    icc_profile: bytes | None
    orientation_exif: bytes | None
```

| 输入 | 解析什么 | 落到 SourceImage |
|------|----------|-------------------|
| JXR | 现有 `decode_jxr` 原样保留 | `primaries=sRGB, reference_white_nits=80`（数值与现状完全一致） |
| PNG | 有无 `cICP`/`iCCP`/`cLLi`（需能读回本工具自己产出的 HDR PNG） | 有 cICP → 查 `cicp.py` 反查表；否则按 sRGB gamma |
| JPEG | 是否为 UltraHDR（APP2 ISO 21496 + MPF） | 有则 Gain Map 还原；否则 baseline sRGB gamma |
| HEIF/AVIF | NCLX（`colr`）、有无 `tmap`/`iref dimg`/`grpl altr` | 同上 |
| JXL | ISOBMFF 容器 CICP、有无 `jhgm` | 同上；复用 `parse_jhgm_bundle` |

### 3.2 唯一归一化函数（新增）

```python
def to_canonical_bt2020_linear(
    src_linear: np.ndarray, primaries: Gamut, reference_white_nits: float,
) -> np.ndarray:
    """任意原生线性 → BT.2020 线性，绝对刻度 1.0 = 10000 nits。"""
    # scale = ref_white / 10000：使 linear==1.0（=ref_white nits）→ canonical 1.0=10000 nits
    # JXR：ref_white=80 → scale=1/125，与现有 scrgb_to_gamut_linear 数值一致
    scale = reference_white_nits / 10000.0
    bt2020 = gamut_linear_to_gamut_linear(src_linear * scale, primaries, Gamut.BT2020)
    return np.clip(bt2020, 0.0, None).astype(np.float32)
```

`gamut_linear_to_gamut_linear` 在 `scrgb_colour.py` 里已存在（`_matrix_gamut_to_gamut` 早就支持任意源→任意目标），只是目前没接到这条路径上。JXR 调用 `(sRGB, 80)` 时结果与现状 `scrgb_to_gamut_linear(scrgb, Gamut.BT2020)` 数值完全一致——这一步是纯重构。

> **注意**：早期草案曾误写 `scale = 10000 / reference_white_nits`（方向反了）；正确缩放是 `reference_white_nits / 10000`，与 `_SCRGB_TO_HDR_LINEAR_SCALE = 1/125` 对齐。

### 3.3 Gain Map 输入：demux，不是重写一遍数学

需要新增（复用 `isobmff_gainmap.py` 已有的 `_find_box`/`_child_boxes`/`_parse_iloc`/`_parse_infe` 等 box 级原语）：

- `gainmap_tmap.py` 补一个反向解析函数（读 `tmap` payload）
- 解析 `iref dimg` / `grpl altr` 找到 base item + gain item
- 一个通用 `apply_gainmap(base_sdr_linear, gain, metadata) -> hdr_linear`（ISO 21496-1 标准公式的逆运算，四种容器共用，只写一次）

得到 `hdr_linear` 后再走 `to_canonical_bt2020_linear`。

### 3.4 SDR（无 HDR 元数据）输入

约定 SDR 参考白 = **100 nits**（贴近 BT.2408 图形白定义），作为新增的源码常量记录在 `docs/PROJECT.md` §9.2 风格的常量表里。SDR 输入可无缝走同一条归一化路径；SDR→SDR 时退化为普通 gamma 域直转。

### 3.5 常见但非本项目原生的具名色彩空间（ProPhoto / AdobeRGB / DCI-P3 等）

`SourceImage.primaries` 不能只在 `{sRGB, P3(Display P3), BT.2020}` 三个内建 `Gamut` 里选，现实输入（尤其是 PNG/JPEG/TIFF 这类静态图，常见于摄影/印刷来源）会带着一批"这个工具原生没有、但在生态里很常见"的色彩空间标识。这些不需要逐个特判写代码，而是按下面的**登记表 + 通用三元组模型**统一接入。

**色彩空间 = 原色矩阵 + 白点 + TRC 的三元组**。项目现有 `Gamut` 枚举其实只表达了"原色矩阵"，白点被隐式假设成 D65（三个内建色域恰好都是 D65，所以能用 `chromatic_adaptation_transform=None` 走捷径），TRC 由独立的 `TransferCurve` 表达。要支持外部色彩空间，必须把"白点"显式化，不能再隐式假设。

**常见色彩空间对照**（原色坐标 `colour-science` 均已内置，不需要自己重新推导）：

| 色彩空间 | 原色（近似） | 白点 | 惯用 TRC | CICP `color_primaries` | 与 BT.2020 关系 |
|----------|--------------|------|----------|--------------------------|------------------|
| sRGB / BT.709 | R(0.640,0.330) G(0.300,0.600) B(0.150,0.060) | **D65** | sRGB 分段曲线 | 1 | 内建，⊂ BT.2020 |
| Display P3（项目 `Gamut.P3`） | R(0.680,0.320) G(0.265,0.690) B(0.150,0.060) | **D65** | sRGB 分段曲线 | 12 | 内建，⊂ BT.2020 |
| **DCI-P3**（影院母版） | 与 Display P3 相同 | **"DCI白" (0.314,0.351)，≈6300K，非 D65 也非 D50** | **Gamma 2.6**（不是 sRGB 曲线） | **11**（注意与 Display P3=12 是两个不同代码点） | ⊂ BT.2020，但白点/TRC 都不同于项目内建 P3 |
| BT.2020（项目 `Gamut.BT2020`） | R(0.708,0.292) G(0.170,0.797) B(0.131,0.046) | D65 | PQ/HLG/BT.1886 | 9 | 内建，本身即 canonical |
| **Adobe RGB (1998)** | R(0.640,0.330) G(0.210,0.710) B(0.150,0.060) | D65（与项目一致，**不需要 CAT**） | **纯 Gamma 2.2**（不是 sRGB 分段曲线） | 无 CICP 代码点，只能靠 ICC 识别 | ⊂ BT.2020，比 sRGB 宽（尤其绿色） |
| **ProPhoto RGB** | R(0.735,0.265) G(0.160,0.840) B(0.037,0.000) | **D50** | ~1.8 gamma（含线性 toe） | 无 CICP 代码点 | **不⊂ BT.2020**（约 90% vs 63% 可见色域），§3.5b 已详述 |
| Adobe Wide Gamut RGB / eciRGB v2 | 更宽/印刷向 | 常见 D50 | Gamma 2.2 | 无 CICP 代码点 | 同 ProPhoto 量级问题，处理方式一致 |
| ACEScg (AP1) / ACES2065-1 (AP0) | 极宽（AP0 覆盖近全部可见色域） | ACES 白点（≈D60，非 D65/D50） | 线性 / 无 TRC | 无 CICP 代码点 | 同上，按"超宽 tier"统一处理 |

`DCI-P3` 和 `Display P3` 是个很好的教学案例：**原色坐标完全相同**，唯一区别是白点和惯用 TRC——证明"原色三角形"不足以定义一个色彩空间，必须是"原色+白点+TRC"三元组，这也是为什么 `SourceImage` 不能只存一个 `Gamut` 枚举值。

**识别路径（谁告诉我们源是哪个色彩空间）**，按优先级：

1. **CICP/NCLX 代码点**（AVIF/HEIF/JXL 常见）：直接查表，覆盖 H.273 里定义的少数几个（1/9/11/12/...）。
2. **ICC profile 描述字符串**（`desc` tag，PNG/JPEG/TIFF 常见）：模糊匹配已知名字（"ProPhoto RGB"、"Adobe RGB (1998)"、"DCI-P3" 等），命中登记表直接拿 `colour.RGB_COLOURSPACES[name]` 的原色/白点/TRC 定义——**不需要自己重新推导矩阵**，colour-science 已经内置了这些色彩空间的精确定义。
3. **ICC 数值标签兜底**：描述字符串匹配不到时，直接从 ICC 的 `rXYZ`/`gXYZ`/`bXYZ`/`wtpt` 标签读出精确原色坐标与白点，按数值近似匹配登记表（容差内认成已知色彩空间），或者匹配不到就构造一个匿名的 `colour.RGB_Colourspace`（不需要有名字，只要有原色矩阵+白点+TRC 就能走通用归一化管线）。
4. **完全没有色彩元数据**：约定默认假设 sRGB（绝大多数软件的实际默认行为），GUI/CLI 给出"未检测到色彩元数据，已假设 sRGB"提示，允许用户手动覆盖。
5. **LUT-based ICC（非纯矩阵+TRC，如印刷 CMYK 相关的复杂 profile）**：退化为用 littlecms（如果运行环境可用）转到锚点色彩空间（sRGB 或 XYZ PCS）再进归一化管线；littlecms 不可用时报警告并按 sRGB 近似处理，不能静默出错。

**归一化管线不区分"内建三色域"还是"新识别到的外部色彩空间"**，统一走同一条路径：

```
TRC 反解码（EOTF/解 gamma，纯 power / sRGB 分段 / 自定义 LUT）
  → 该色彩空间下的显示线性
  → 白点 ≠ D65 时做 CAT（Bradford/CAT02）适配到 D65
  → 矩阵转换到 BT.2020（_matrix_gamut_to_gamut 泛化：任意已注册色彩空间 → BT.2020）
  → 若该色彩空间比 BT.2020 更宽（ProPhoto/AdobeWideGamut/AP0/AP1）末尾裁剪；否则直接得到 canonical 值
```

具体到归一化函数：`chromatic_adaptation_transform` 从 `None` 换成 `"Bradford"`（白点相同时是恒等变换，不影响项目内建三色域现状；白点不同时才生效）：

```python
def to_canonical_bt2020_linear(src_linear, colourspace, reference_white_nits):
    scale = reference_white_nits / 10000.0
    bt2020 = colour.RGB_to_RGB(
        src_linear * scale, colourspace, "ITU-R BT.2020",
        chromatic_adaptation_transform="Bradford",
    )
    return np.clip(bt2020, 0.0, None).astype(np.float32)
```

**不需要为 DCI-P3/AdobeRGB/ProPhoto/AP0 各写一段特判代码**——只要解码器把"原色矩阵 + 白点 + TRC"三元组正确地喂进 `SourceImage`，剩下的路径对所有色彩空间一视同仁。新增支持一个色彩空间，本质上是"在识别登记表里加一行映射"，不是新写归一化逻辑。

### 3.6 ProPhoto/超宽色彩空间的两个专属问题（复述，见 §6.5 完整推导）

见 §6.5：① 色域收窄裁剪不可避免；② D50/ACES 白点必须做 CAT，不能复用"项目内建三色域共享 D65"这个捷径。

### 3.7 直通优化（与色彩架构无关的独立优化）

`convert_file` 顶部：输入输出格式相同且没有要求任何色域/曲线/位深变化时，直接字节级拷贝，跳过解码/编码，保证零质量损失、最快速度、不丢失原始 ICC/EXIF。

### 3.8 Gain Map 输入 + Gain Map 输出的取舍

- **重算路径（v1 默认/正确性基线）**：demux 出 HDR linear → 走统一 L1b 重新按目标 tone map/色域算一遍。
- **remux 直接转容器（后续可选优化）**：不碰像素，只搬运 base+gain 两路裸流。只有目标格式位深/色域恰好兼容时才可用，是 M×M 组合，**v1 不做**，等主线跑通再按高频格式对加白名单 fast path。

---

## 4. 落地里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| A | 新增 `SourceImage` + `to_canonical_bt2020_linear`；JXR 路径重构为"解码器产出 SourceImage → 归一化"，验证数值零回归 | ✅ |
| B | `cicp.py` 加反查表（CICP 代码点 → Gamut/TransferCurve）；PNG / 普通 JPEG 的 SDR + HDR(cICP) 解码器 | ✅ |
| C | HEIF/AVIF/JXL 的 Direct（NCLX/CICP）解码器 | ✅ |
| D | 四种格式的 Gain Map demux（`apply_gainmap` + 各容器 box 反解析） | ✅ |
| D2 | 命名色彩空间登记表 + ICC 数值标签解析（§3.5）：`SourceImage.primaries` 泛化为"原色+白点+TRC"三元组；CAT（Bradford）接入归一化函数；覆盖 ProPhoto / AdobeRGB / DCI-P3 等常见外部色彩空间 | ✅ |
| E | `convert_file` 接入格式自动识别（魔数而非仅扩展名）+ 解码器分发；直通优化 | ✅ |
| F（可选） | 高频组合的 Gain Map remux 快速通道 | 待办 |
| G | GUI 预览层适配（见 §5） | ✅ |
| H | 统一元数据管理（§7）：`core/metadata.py` 的 `MetadataBag` + 各格式 extract/embed；`SourceImage.metadata` 挂载；解码时方向转正；`metadata_policy` 参数；隐私清除开关 | 待办 |
| H2 | Gain Map demux（§3.3）补充 `hdrgm:` XMP 解析路径（§7.5），汇聚到统一 `GainmapMetadata` | 待办 |

---

## 5. GUI HDR 预览如何适配（不依赖"统一转 scRGB"）

### 5.1 核心结论

`scRGB` 在预览这条路上不是"业务层选的通用色彩模型"，而是 **Windows D3D11 HDR 交换链本身的强制契约**：

```1:1:src/hdr_converter/gui/hdr_d3d11.py
"""Win11 D3D11 FP16 scRGB 交换链，供嵌入 QWidget 的 HDR 预览。"""
```

交换链格式对应 `DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709`（线性 + sRGB 基色 + 1.0≈80nit），这是操作系统合成器认的格式，与"转换器内部用什么色彩模型做归一化"完全独立。所以 §3 里把 L0 从"JXR scRGB"泛化成"BT.2020 绝对线性"这个决定，**不影响**预览能否工作——预览只需要在"呈现的最后一步"把 canonical L0 转成 D3D 要的 literal scRGB。这个反向转换函数已经存在，不用新写：

```161:172:src/hdr_converter/core/color_pipeline.py
def bt2020_linear_to_scrgb(bt2020_linear: np.ndarray) -> np.ndarray:
    """BT.2020 显示线性（1.0=10000 nits）→ scRGB 线性（与 scrgb_to_gamut_linear 互逆）。"""
```

`preview_frame.py::linear_to_preview_scrgb` 也已经是同一件事（目标色域线性 1.0=10000nit → scRGB sRGB 线性 1.0≈80nit）；Stage G 后 HDR/SDR 预览统一走该路径。

### 5.2 需要改的地方（已完成 — Stage G）

> 以下为设计当时的改造说明，**现已落地**。可选「亮度校准」分支其后已从代码中移除。

1. **解码缓存泛化**：`decode_cache.py::JxrDecodeCache` 改为存 canonical L0（BT.2020 绝对线性），而不是"JXR 原生 scRGB"。`load_jxr_raw` 泛化成 `load_source_raw(path, cache, decoder_registry)`，按扩展名/魔数分发到 §3 的解码器，产出 `SourceImage` → 立即归一化 → 缓存归一化结果。**预览和转换共用同一份缓存**，这一点架构不变。
2. **`preview_frame.py` 入口**：函数签名改为接收 canonical BT.2020 缓冲；canonical→目标色域后经 `linear_to_preview_scrgb` 呈现。
3. **HDR 分支统一路径**：始终走 `canonical → linear_to_preview_scrgb`（不再透传原生 scRGB）。作用对象是短边 ≤1080 的 L2 缓冲，成本可接受。
4. **非 HDR 输入的预览门槛**：`SourceImage.is_hdr=False`（普通 SDR JPEG/PNG）时，不必启用 D3D HDR 交换链，直接走现有 SDR `ImageLabel` 分支（`preview_worker.py` 已经有 `need_sdr`/`need_hdr` 开关，只是加一个"按 is_hdr 门控"的判断，不是新机制）。
5. **Gain Map 输入的预览**：如果输入本身带增益图（如外部工具产出的 UltraHDR JPEG），§3.3 的 demux 已经把它还原成完整 HDR canonical linear，预览端不需要额外特判——复用同一条路径即可看到还原后的 HDR 效果。

### 5.3a 呈现层能否彻底摆脱 scRGB，直接同步 BT.2020？

**不能，这是 DXGI API 的硬限制，不是本项目的架构选择。**

`DXGI_COLOR_SPACE_TYPE` 枚举里，**线性编码（G10）只跟 sRGB/BT.709 基色（P709）配对**——这正是 scRGB 的标准定义（IEC 61966-2-2）。想用 BT.2020 基色（P2020），只能选非线性编码（`G2084`=PQ / `G22`=2.2 gamma / `G24`=2.4 gamma），根本不存在"线性 + BT.2020"这个组合。现有代码用的正是：

```119:119:src/hdr_converter/gui/hdr_d3d11.py
DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709 = 9
```

也就是说，canonical BT.2020 缓冲不管走哪条路呈现，都必须做一次转换——scRGB 路径转基色（矩阵乘法，曲线不变）；PQ/P2020（HDR10）路径转编码（PQ OETF，曲线改变）。"完全不转换直接同步 BT.2020"在 DXGI 层面没有对应枚举值可选。

**PQ/P2020（HDR10）路径不是更好的替代**：① PQ 编码比矩阵乘法贵；② 需要额外通过 `IDXGISwapChain4::SetHDRMetaData` 管理 HDR 元数据；③ 这条路径是给**独占全屏**内容设计的（游戏/播放器绕开 DWM 合成），普通桌面窗口化内容（本项目嵌入 QWidget 的预览面板）走 DWM 合成，官方推荐路径正是 scRGB，由 DWM 负责映射到显示器真实能力；④ 显示器报告支持 P2020 只是 HDR10 信号协议约定，不代表面板物理原色真是 BT.2020，走 scRGB+DWM 合成反而更省心。

结论：预览的**计算**已经同步到 BT.2020（§5.2 的归一化管线），**呈现**这最后一跳转成 scRGB 是必要的、且是对的选择——这一步的角色和 PNG/AVIF 编码器完全一样，都是"把 canonical BT.2020 翻译成某个具体交付契约"，只是这次的契约甲方是 Windows 显示合成器。

### 5.3b JXR 输入时，预览是否变成 scRGB→BT2020→scRGB？

**是的，且这个"往返"只发生在预览呈现这一环，不影响导出。**

```
JXR 解码 → 原生 scRGB（primaries=sRGB, ref_white=80）
  → [矩阵①] to_canonical_bt2020_linear → BT.2020 canonical
  → [矩阵②] linear_to_preview_scrgb（矩阵①的逆） → scRGB → D3D11
```

矩阵①②互为逆矩阵，链起来数学上是恒等变换；旧实现的"直接透传原生 scRGB"快捷路径（零矩阵）被换成了"两次矩阵互相抵消"（两次矩阵）。

**导出侧不受影响**：默认目标色域即为 BT.2020 时 `scrgb_to_gamut_linear` 一次矩阵即可，与重构前等价。"多一趟"是 D3D scRGB 交换链恒定要求 sRGB 基色（§5.3a 的 DXGI 限制）导致的，跟用户选的导出目标色域无关。

**值不值得优化**：不值得现在处理。① 精度：两次互逆矩阵的舍入误差量级 ~1e-6~1e-7，而 D3D 后备缓冲本身是 FP16（有效精度 ~1e-3~1e-4），往返误差比呈现载体自身精度还小两个数量级，显示结果测不出差异。② 性能：按 §6.2 的模型，两次矩阵乘法作用在下采样到短边 ≤1080 的缓冲上，仍是几毫秒量级，相对解码/下采样/tone map/D3D 上传呈现是噪声。若要彻底消除，可以加一个"`SourceImage.primaries == sRGB` 时跳过 canonical 这一跳、只做白点标量缩放"的针对性快捷路径（可证明安全，不引入新正确性分支），但这会在预览代码里重新引入一个格式特判，与"N 解码器 + 1 共享归一化"的架构原则相悖，收益又是"把已经测不出来的开销减半"——建议先不加，等有实测数据支撑再决定。

### 5.3 不用动的部分

- `hdr_d3d11.py` 整个 D3D11 交换链创建、呈现逻辑：**不改**，因为它的输入契约（FP16 scRGB）没变，只是"谁负责把数据转换成这个契约"从"JXR 解码器天然满足"变成"预览层显式转换一次"。
- SDR 预览的 Hable tone map（`_hable_filmic`/`_hable_tone_map_scrgb`）：不改，只是输入源从"原生 scRGB"换成"canonical→scRGB"。
- `preview_panel.py`、`hdr_preview_window.py` 的 UI 编排：不改。

---

## 6. 精度与性能

### 6.1 归一化矩阵的选型依据（承接 §3.2）

`BT2020/P3/sRGB → scRGB → BT2020/P3/sRGB` 不是好选择，原因不是"矩阵变换本身有损"（不裁剪时是精确可逆的线性变换），而是：

1. **现有代码在这一跳的终点有真实裁剪**：

   ```95:99:src/hdr_converter/core/color_pipeline.py
   def scrgb_to_gamut_linear(scrgb: np.ndarray, gamut: Gamut) -> np.ndarray:
       """scRGB → 目标色域 HDR 线性（÷125；保留负值至 XYZ 后再裁非负）。"""
       linear_abs = scrgb_to_gamut_linear_abs(scrgb, gamut)
       hdr = linear_abs.astype(np.float64) * _SCRGB_TO_HDR_LINEAR_SCALE
       return np.clip(hdr, 0.0, None).astype(np.float32)
   ```

   BT.2020/P3 的高饱和色在 sRGB 基色下天然是负值；把这个负值裁剪点复用为"通用中转的第二跳"，会把合法存在于源色域∩目标色域、只是恰好在 sRGB 三角外的颜色提前清零，造成色相/饱和度错误。

2. **sRGB 是三者里最窄的色域**：sRGB ⊂ P3 ⊂ BT.2020（近似），拿最窄的当中转 hub 是矩阵链里最差的选择，该选最大色域（BT.2020）或没有三角边界的 CIE XYZ。`scrgb_colour.py::_matrix_gamut_to_gamut` 已经是"经 XYZ 一次合并矩阵直达目标"的正确模式：

   ```28:35:src/hdr_converter/core/scrgb_colour.py
   def _matrix_gamut_to_gamut(src: Gamut, dst: Gamut) -> np.ndarray:
       """源色域线性 → 目标色域线性（合并矩阵）。"""
       cs_s = colour.RGB_COLOURSPACES[_GAMUT_COLOUR_NAMES[src]]
       cs_t = colour.RGB_COLOURSPACES[_GAMUT_COLOUR_NAMES[dst]]
       return np.asarray(cs_t.matrix_XYZ_to_RGB @ cs_s.matrix_RGB_to_XYZ, dtype=np.float64)
   ```

3. **语义耦合**：`scrgb_to_gamut_linear_abs` 这套函数名字/实现绑死了"源=sRGB 基色、1.0=80nit"的 JXR 专属语义（`_SCRGB_TO_HDR_LINEAR_SCALE=1/125`），复用它做通用中转等于借旧名字写新函数，不如新增显式通用、单跳的 `to_canonical_bt2020_linear`。

### 6.2 性能模型

- 归一化矩阵是 3×3，作用于 4K 图（≈830 万像素）时是 NumPy 向量化的几毫秒~十几毫秒量级；对比 `STATUS.md` §5 记录的真实基准（UHDR 编码 ~2185ms、`convert_file` 含解码 ~2387ms），占比 <1%，不会成为瓶颈。
- 预览侧运算全部发生在下采样到短边 ≤1080 的 L2 缓冲上，成本与矩阵色域转换同量级，文档已记录"可接受"。
- 真正的新增耗时变量是**各格式解码/编码库本身**（HEIF/AVIF 走 HEVC/AV1 编解码，比 JPEG/PNG 慢一个量级）——这部分开销与色彩管线架构选择无关，不管"共享 canonical"还是"各自实现"都躲不掉。
- Gain Map demux：box 解析近似免费；`apply_gainmap` 的 tone 曲线运算量级与现有 SDR tone map 前向运算相同，可比照现有 `run_parallel_pair` 并行化。
- 并行调度直接复用 `core/parallel.py`（`run_parallel_pair`、`batch_workers`、缓存命中时强制单进程避免大数组跨进程 pickle），泛化后原样适用，不需要新设计。

### 6.3 精度：能否一比一还原（按场景拆分）

| 场景 | 能否一比一还原 | 原因 |
|------|----------------|------|
| 输入输出格式相同、参数未变 | 能，字节级相同 | 走 §3.5 的直通拷贝优化 |
| 同色域/曲线/位深的 decode→canonical→encode 往返 | 数值等价（非 bit-exact），误差 ~1e-5 相对误差 | 与 `PROJECT.md` §7 已有验收标准（Horizon 样张 max diff ≈ 1.5×10⁻⁵）同量级，远小于任何量化步长 |
| 真正的色域收窄转换（如 BT.2020 PQ → sRGB gamma） | 不能，也不应该能 | 源色域内、目标色域外的颜色必然被裁剪/色域映射，这是色彩管理的正确行为 |
| 位深下降 / 有损编码格式自身量化 | 不能 | 格式/位深本身决定，与色彩管线架构无关 |
| Gain Map "demux→重算"路径 | 不能精确复现原图的 SDR 基础图 | 用本项目自己的 tone map 算子重新计算，不等于原工具当时用的算子；只有 remux（v1 不做）才能字节级还原 |
| 第三方任意 ICC 输入 | 有近似误差风险 | 若只按 CICP 近似而非真正 ICC 解析，属于"元数据读取精度"问题，见 §8 |

### 6.4 验证计划

1. 架构重构回归：JXR 路径前后对比，容差沿用 `PROJECT.md` §7 的 1.5×10⁻⁵ 量级标准。
2. 新解码器精度：`decode→canonical→同参数重新导出` 往返，与独立参考实现（如 Pillow/libavif 官方 CLI 解码同一文件）比较，容差按该格式自身有损压缩量级设定。
3. 性能基准：复用 `scripts/_benchmark_jpg_export.py` 的分阶段测速模式，给每个新解码器加同款脚本，验证归一化步骤是否真的是噪声级别。

### 6.5 canonical 色域选型的边界情况：BT.2020 不是绝对超集

选 BT.2020 当 L0 工作缓冲区，不是因为它数学上保证覆盖所有可能输入，而是权衡后的实用选择：

- **矩阵枢纽已经是 XYZ**（`_matrix_gamut_to_gamut` 内部经 XYZ 合并），"要不要选更宽的具名色域"这个问题只关系到 **L0 缓冲区本身用什么数值表达**，不关系到矩阵计算精度。
- 选更宽的色域（ACES AP0 / ProPhoto RGB）当工作缓冲没有实质收益：① 下游 Hable/Chrome/Safari tone map、MaxCLL 都是 max-RGB 逐通道运算，只有 RGB 分量对应真实显示原色时才有感知意义，AP0/ProPhoto 的虚色原色没有这个语义；② 本项目能写的输出元数据（cICP/NCLX）只覆盖 `{sRGB, P3, BT.2020}`（ITU-T H.273 没有 AP0/ProPhoto 代码点），再宽也躲不开编码前必须收窄这一步，只是把裁剪从解码时延后到编码时，白白多背一路没用的数值范围；③ 消费级 HDR 生态（HDR10/HLG/UltraHDR/AVIF/HEIF/JXL）全部以 BT.2020 或更窄色域为参考，ProPhoto/AP0 不是这个工具的目标输入场景主体。

**但 BT.2020 确实不是所有输入的严格超集**——ProPhoto RGB 是现成反例（覆盖约 90% 可见色域，BT.2020 约 63%），需要单独处理两个问题：

1. **色域收窄（裁剪）**：ProPhoto 内、BT.2020 外的颜色必须裁剪/色域映射，这是不可避免、也正确的行为——但要在归一化这一步**显式、一次性**完成，不能像"经 sRGB 中转"那样意外发生在无关代码路径里。
2. **白点不匹配（更隐蔽）**：ProPhoto 标准白点是 **D50**，sRGB/P3/BT.2020 都是 **D65**。现有代码到处显式 `chromatic_adaptation_transform=None`，这只因为项目内三个色域共享 D65 才安全；直接复用到 ProPhoto 会导致灰阶偏色（忘做色度适应的典型翻车）。

**接入方式（不破坏 N 解码器 + 1 归一化函数的架构）**：

- `SourceImage.primaries` 泛化为可承载"任意具名 colour-science 色彩空间或自定义原色矩阵+白点"，不再局限于 `Gamut` 枚举三选一；
- ProPhoto 只能靠**解析嵌入的 ICC profile** 识别（CICP 无代码点），这是 §8 "第三方 ICC 需要真正解析"这条开放风险的具体触发场景，不是新增风险；
- 归一化函数把 `chromatic_adaptation_transform=None` 换成 `"Bradford"`（或 CAT02）：白点相同时（项目内三个色域之间）是恒等变换，不影响现状；只有真正遇到 D50 源时才生效，修正白点后再做色域收窄裁剪。

```python
def to_canonical_bt2020_linear(src_linear, colourspace, reference_white_nits):
    scale = reference_white_nits / 10000.0
    bt2020 = colour.RGB_to_RGB(
        src_linear * scale, colourspace, "ITU-R BT.2020",
        chromatic_adaptation_transform="Bradford",
    )
    return np.clip(bt2020, 0.0, None).astype(np.float32)
```

不需要为 ProPhoto（或任何 D50 参考的 ICC 输入）单独写特判代码，解码器只要正确报告原生原色矩阵和白点，归一化函数天然处理。

---

## 7. 统一元数据管理（EXIF / XMP / ICC / IPTC）

### 7.1 需要一个元数据管理器，但职责要收窄

**结论：需要**，但它不应该是"深度理解每种元数据语义"的复杂系统，而是和色彩管线同一个套路——**N 个格式的 extract 函数 + 1 个格式无关的 `MetadataBag` + M 个格式的 embed 函数**，绝大部分内容**只搬运字节，不关心内容**；只有极少数字段（方向、Gain Map 相关的 XMP）需要真正的语义介入。

```python
@dataclass
class MetadataBag:
    exif: bytes | None            # 原始 TIFF IFD 结构（剥掉各容器自己的包装头/item 包装）
    xmp: bytes | None             # 原始 XML packet 字节
    iptc: bytes | None            # IPTC-IIM 二进制块（较少见，仍需兼容老照片工作流）
    icc_profile: bytes | None     # 原始 ICC profile 字节（非本工具生成的 per-gamut ICC）
    orientation: int = 1          # 从 EXIF Orientation tag 解析出的语义值，独立字段，不藏在 exif blob 里
    gainmap_xmp: GainmapXmpMeta | None = None  # 识别到 Google 风格 hdrgm: 命名空间时解析出的语义字段
```

挂载点：作为 `SourceImage`（§3.1）的一个字段 `SourceImage.metadata: MetadataBag`，跟色彩数据同批产出，但**完全不参与**归一化/色域转换/tone map 这些数学运算——色彩管线看不到它，只有解码器（写入）和编码器（读出）触碰它。

### 7.2 元数据分类与处理策略

| 类型 | 深度解析？ | 处理策略 | 关键点 |
|------|------------|----------|--------|
| EXIF（相机参数、拍摄时间、GPS 等绝大部分 tag） | 不需要 | 原样透传字节 | 唯一的例外是 Orientation tag，见 §7.3 |
| **EXIF Orientation** | **需要** | 解码时转正像素，写出时不再需要该 tag | 见 §7.3 |
| EXIF 内嵌缩略图 | 不需要 | 默认丢弃（缩小体积），可选保留 | 转换后主图本身就是缩略图的替代品 |
| XMP（关键字、评级、Lightroom 编辑历史等） | 不需要 | 原样透传字节 | 唯一的例外是 Gain Map 相关命名空间，见 §7.5 |
| **XMP `hdrgm:` 命名空间（Google Ultra HDR 元数据）** | **需要** | 作为 Gain Map demux 的备选/补充数据源 | 见 §7.5，这不是"顺手做的好事"，是真正的互操作性缺口 |
| IPTC-IIM | 不需要 | 原样透传字节 | 老照片工作流仍在用，兼容成本很低 |
| ICC profile | 视场景 | 见 §7.4 的策略冲突 | 与项目已有的"自生成 per-gamut ICC"有直接冲突，需要显式策略 |
| GPS 坐标等隐私敏感字段 | 不需要解析，但需要**可选清除** | 提供"清除全部元数据"开关 | 见 §7.7 隐私考虑 |

### 7.3 关键交叉点 1：方向（Orientation）

EXIF Orientation tag（0x0112）不能像其它 tag 一样"盲目透传"，因为它直接影响像素该怎么摆——而且很多新兴 HDR 格式的阅读器（尤其是这个工具主打的 PQ/HLG/Gain Map 输出）对 EXIF Orientation 的支持参差不齐，"透传 tag、指望下游认"这个策略风险很高。

**建议策略（默认行为）**：**解码时按 Orientation 物理转正像素**（`np.rot90`/翻转，纯几何操作），写出时不再携带该 tag（或写 `1`=normal）。这是 Chrome 等主流工具处理"来源不明确是否会被正确解读方向"内容时的通用做法。

转正操作应该发生在 `SourceImage` 产出**之后、归一化之前**——是纯几何变换，跟色彩数值无关，顺序上放哪里都不影响颜色结果，但放在归一化之前能保证 L0 之后的整条流水线（色彩管线 + GUI 预览）都不需要再关心方向问题，逻辑更干净。

### 7.4 关键交叉点 2：ICC 透传 vs 项目自生成 ICC 的策略冲突

项目现有 `assets/apple_baseline_icc.py`/`libjxl_pq_icc.py` 会**自己生成** per-gamut ICC。如果同时支持"透传原始 ICC"，两者什么时候用哪个必须显式定义，不能隐式猜：

| 场景 | 策略 | 原因 |
|------|------|------|
| 发生了真正的色彩转换（gamut/curve/位深变了） | **必须用项目自生成的 ICC/cICP/NCLX** | 色彩空间都变了，旧 ICC 描述的是错误的信息，透传等于撒谎 |
| 纯直通（§3.7，格式和参数都不变） | 原始 ICC 自然随字节拷贝保留 | 不涉及这个决策，属于直通的一部分 |
| **格式变了，但色彩参数没变**（如 sRGB PNG → sRGB JPEG，用户未要求任何 gamut/curve 改变） | 目标容器**支持任意 ICC blob** 时（PNG iCCP/JPEG APP2/TIFF）→ 优先透传原始 ICC；只支持 NCLX 代码点的模式（本项目 HEIF/AVIF 目前的策略）→ 退化为 CICP 近似 | 原始 ICC 可能包含比"标准三色域"更精确的校准信息（比如显示器厂商自定义 profile），没有实际颜色变化时没理由用更粗糙的近似替换它 |

需要一个显式的 `metadata_policy` 参数（比如 `preserve_icc_when_unchanged` / `always_regenerate`），而不是让这个决策隐藏在某个函数的默认行为里。

### 7.5 关键交叉点 3：Gain Map 的 XMP 元数据——不是加分项，是必要的互操作性

§3.3 设计的 Gain Map demux 目前只考虑了**本项目自己写出时用的** ISO 21496 APP2 二进制元数据格式。但现实生态里，相当一部分 Ultra HDR JPEG（尤其是 Google Camera / Android 较早版本、部分 Adobe 工具）用的是 **XMP `hdrgm:` 命名空间**（`GainMapMin`/`GainMapMax`/`Gamma`/`OffsetSDR`/`OffsetHDR`/`HDRCapacityMin`/`HDRCapacityMax` 等字段）描述增益图参数，而不是（或者附加于）ISO 21496 二进制块。

**这意味着**：如果 Gain Map demux 只认 APP2 二进制格式，会读不懂相当一部分"野生"Ultra HDR JPEG——这不是元数据管理的锦上添花，而是 §3.3 demux 设计里一个真实的输入缺口，需要补一条"解析 `hdrgm:` XMP 作为 `apply_gainmap` 的备选元数据源"的路径。两种来源解析出的语义字段应该汇聚到同一个 `GainmapMetadata`（`gainmap_math.py` 已有的结构）上，下游 `apply_gainmap` 不需要关心元数据到底来自 APP2 二进制还是 XMP。

### 7.6 各容器的元数据挂载位置（编码器需要知道的搬运细节）

| 格式 | EXIF | XMP | ICC | IPTC |
|------|------|-----|-----|------|
| PNG | `eXIf` chunk（PNG 1.5+，注意兼容性：不是所有工具都识别） | `iTXt` chunk，keyword=`XML:com.adobe.xmp` | `iCCP` chunk（项目已在用） | 少见，通常不单独支持 |
| JPEG | APP1 段，`"Exif\0\0"` 前缀 + TIFF 结构 | 另一个 APP1 段，`"http://ns.adobe.com/xap/1.0/\0"` 前缀（与 EXIF 的 APP1 用不同签名区分） | APP2 段（项目 `baseline_icc.py` 已在用；大 ICC 需要按标准分段，单段上限 64KB） | APP13 段（`"Photoshop 3.0\0"` + 8BIM 资源） |
| HEIF/AVIF（ISOBMFF） | 独立 `Exif` type item，经 `cdsc` iref 关联主图 | 独立 `mime` type item（MIME `application/rdf+xml`），同样经 `cdsc` iref | `colr` box（`prof`/`rICC` 类型） | 无标准位置，通常不支持 |
| JXL | 容器 `Exif` box | 容器 `xml ` box | 已有色彩管线覆盖 | 无标准位置 |
| JXR | 少见（截图工具产出，一般不带相机 EXIF），JPEG XR 容器本身是 TIFF-like IFD 结构，理论上可以有 | 少见 | 不适用（本项目专属场景） | 不适用 |

HEIF/AVIF 那一列的 `Exif`/`mime` item + `cdsc` iref 机制，跟 `isobmff_gainmap.py` 里已有的 `_iloc_box`/`_infe_box`/`_iref_dimg` 这套 box 原语是**同一套底层机制**，直接复用，不需要新写 ISOBMFF 解析代码。

### 7.7 隐私考虑

EXIF 常带 GPS 坐标、设备序列号等敏感信息。作为"全能转换器"，应该提供一个**用户可见的开关**："保留全部元数据 / 仅保留方向和 ICC（去隐私）/ 清除全部元数据"，而不是默认无条件透传——这跟很多主流图片处理工具（包括社交平台上传时的默认行为）的隐私预期一致。

### 7.8 性能与体积影响

EXIF/XMP/ICC/IPTC 都是相对小的字节块（几 KB 到几十 KB；XMP 若含长编辑历史或 EXIF 内嵌缩略图可能到几百 KB），透传成本可忽略，不需要专门优化；唯一要注意的实现细节是"目标容器的分段限制"（比如 JPEG APP2 单段 64KB 上限，超限需要标准的多段拼接），这是搬运格式的工程细节，不是性能问题。

### 7.9 落地方式

已并入 §4 落地里程碑表的 H / H2 两个阶段。

---

## 8. 开放风险 / 待决问题

- 非本工具产出的 HDR PNG/HEIF/AVIF/普通照片可能携带**任意第三方 ICC profile**（ProPhoto/AdobeRGB/DCI-P3 等常见色彩空间的接入设计见 §3.5，已有具体方案）；**残余风险**是 LUT-based（非纯矩阵+TRC）的复杂 ICC profile（常见于印刷 CMYK 相关工作流），§3.5 的兜底策略依赖 littlecms 可用性，littlecms 不可用时只能近似处理并提示用户，不保证精确还原。
- HLG 读回时要与写出保持同一套"不做 BT.2100 OOTF⁻¹"的静帧惯例，否则读写双方语义不对称。
- EXIF 方向、XMP 等元数据的 passthrough 策略（转换时是否保留、是否重新计算方向后旋转像素）待定。
- 各格式解码库的运行时依赖（pillow-heif、libavif、libjxl 通过 imagecodecs）在 PyInstaller 冻结环境下的可用性需要和现有 `is_jxr_supported()` 风格的运行时检测一起补齐。
