# HDR 格式转换器

多格式 HDR/SDR 互转工具（包名 `hdr-converter`）。支持 **JXR / PNG / JPEG / HEIF / AVIF / JXL** 输入，导出 PNG、Ultra HDR JPEG、HEIF、AVIF、JPEG XL（**不含 JXR 写出**）；色域 sRGB / Display P3 / BT.2020。

**正式验收以 PQ 曲线为准**；HLG / Linear 等为实验性支持。详见 [docs/STATUS.md](docs/STATUS.md)。

## 功能

- **输入**: JXR、PNG、JPEG、HEIF/HEIC、AVIF、JXL（含 Ultra HDR / Gain Map demux）
- **输出**: PNG、HEIF、AVIF、JPG (Ultra HDR)、JXL — **PQ 已验收**；无 JXR 写出
- **色域 / 曲线**: sRGB、Display P3、BT.2020；PQ（正式）；HLG / Linear / sRGB（实验性）
- **HDR 交付**: Direct（cICP/NCLX）或 Gain Map mono/color
- **界面**: Fluent Windows GUI（多格式拖放；Win32 可选 D3D HDR 预览）
- **CLI**: 同参数集批处理
- **可选 RTX**：NVIDIA TrueHDR / VSR（侧栏「HDR 超分」；需编译桥接 DLL，见 [docs/RTX_VIDEO.md](docs/RTX_VIDEO.md)）

## 安装

推荐清华大学 PyPI 镜像：

```bash
python -m venv .venv
.venv\Scripts\activate
set PIP_CONFIG_FILE=pip.conf
pip install -r requirements.txt
```

或：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

HEIF 读写需要 `pillow-heif`（已在 `requirements.txt`）。

## 运行

```bash
python -m hdr_converter
```

或安装后：

```bash
hdr-converter
```

## 命令行

```bash
python -m hdr_converter.cli input.avif -o output.png --format png --gamut p3 --curve pq --level 2
python -m hdr_converter.cli input.jxr -o output.jpg --format jpg --gamut bt2020 --curve pq --hdr-delivery gainmap_mono --level 90
```

常用参数：`--quantize-bits`、`--level`、`--gainmap-scale`、`--sdr-tonemap`、`--jpeg-subsampling`

RTX（需 `hdr_rtx_bridge.dll`）：

```bash
python -m hdr_converter.cli input.jpg -o out.avif --format avif --rtx-enhance thdr --rtx-max-luminance 1000
```

## 文档

| 文档 | 内容 |
|------|------|
| [docs/STATUS.md](docs/STATUS.md) | **验收状态、默认参数、路线图** |
| [docs/RTX_VIDEO.md](docs/RTX_VIDEO.md) | NVIDIA RTX Video（TrueHDR / VSR） |
| [docs/PROJECT.md](docs/PROJECT.md) | 产品定位、架构、管线、可配置参数 |
| [docs/MULTI_FORMAT_PLAN.md](docs/MULTI_FORMAT_PLAN.md) | 多格式扩展方案 |
| [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) | 分阶段执行与验收 |
| [docs/UNIFIED_PIPELINE.md](docs/UNIFIED_PIPELINE.md) | 统一导出管线 |
| [docs/ICC_PROFILES.md](docs/ICC_PROFILES.md) | ICC 生成与嵌入策略 |
| [docs/CHECKPOINT_PNG.md](docs/CHECKPOINT_PNG.md) | PNG 定稿 |
| [docs/CHECKPOINT_JPEG_PQ.md](docs/CHECKPOINT_JPEG_PQ.md) | Ultra HDR JPEG（PQ） |
| [docs/CHECKPOINT_JPEG_HLG.md](docs/CHECKPOINT_JPEG_HLG.md) | Ultra HDR JPEG（HLG，实验性） |
