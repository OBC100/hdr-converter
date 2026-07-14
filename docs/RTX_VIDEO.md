# NVIDIA RTX Video（TrueHDR / VSR）集成

本项目可选使用 **RTX Video SDK** 在导出前对 SDR 输入做：

- **TrueHDR**：SDR → scRGB HDR（浮点，1.0≈80 nits）
- **VSR**：AI 超分（1× / 2× / 4×）
- **VSR + TrueHDR**

GUI 侧栏分组：**HDR 超分（RTX）**。CLI：`--rtx-enhance thdr|vsr|vsr_thdr`。

## 要求

| 项 | 说明 |
|----|------|
| GPU | GeForce **RTX 20** 及以上 |
| 驱动 | 建议 570+（与 TouchDesigner RTX TOP 一致） |
| SDK | [RTX Video SDK](https://developer.nvidia.com/rtx-video-sdk) |
| CUDA | Toolkit 12.x |
| 桥接 DLL | 编译 `native/rtx_bridge` → `hdr_rtx_bridge.dll` |

## 编译桥接

```powershell
# 1. 解压 SDK，设置环境变量到含 include / bin / lib 的根目录
$env:NV_RTX_VIDEO_SDK = "D:\SDKs\RTX_Video_SDK"

# 2. 编译
cd native\rtx_bridge
.\build.ps1
# 产物：native\rtx_bridge\out\hdr_rtx_bridge.dll
# 并尽量复制 nvngx_truehdr.dll / nvngx_vsr.dll 到同目录
```

运行时查找顺序：`HDR_RTX_BRIDGE` 环境变量 → `native/rtx_bridge/out/` → 当前目录 / 包目录。

## 色彩约定

- **输入**：管线 scRGB → 压成 SDR 显示域约 [0,1] 再送 SDK  
- **TrueHDR 输出**：scRGB 扩展范围（白点约 `peak_nits/80`）→ 直接进入现有 Direct / Gain Map  
- **位深**：SDK 效果层为浮点；导出 10/12/16-bit 仍由「有效位深」决定  

## 调参（与 NVIDIA App / TouchDesigner 一致）

| 参数 | 默认 | 含义 |
|------|------|------|
| Contrast | 125 | 反差 |
| Saturation | 100 | 饱和度 |
| Middle Gray | 25 | 中灰曝光 |
| Max Luminance | 1000 | 峰值 nits |
| VSR Quality | High | Bicubic…Ultra |
| VSR Scale | 2 | 输出倍率 |

未编译桥接时，选择非「关闭」会报错并提示本文档步骤（不会静默跳过）。

## 参考

- [TouchDesigner/NVIDIARTXVideoTOP](https://github.com/TouchDesigner/NVIDIARTXVideoTOP)  
- [RTX Video SDK](https://developer.nvidia.com/rtx-video-sdk)  
