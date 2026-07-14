/**
 * hdr_rtx_bridge — 将 NVIDIA NGX TrueHDR / VSR 暴露为 C ABI，供 Python ctypes 调用。
 *
 * 参考：TouchDesigner/NVIDIARTXVideoTOP（CUDA + NGX_CUDA_*_TRUEHDR / VSR）
 * 输入：RGBA float32，SDR 显示域约 [0,1]
 * TrueHDR 输出：scRGB 线性扩展（1.0≈80 nits）
 */
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <cuda.h>
#include <cuda_runtime.h>

#include <nvsdk_ngx.h>
#include <nvsdk_ngx_defs.h>
#include <nvsdk_ngx_helpers_truehdr.h>
#include <nvsdk_ngx_helpers_vsr.h>

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#ifdef HDR_RTX_BRIDGE_EXPORTS
#define RTX_API __declspec(dllexport)
#else
#define RTX_API __declspec(dllimport)
#endif

extern "C" {

RTX_API int rtx_bridge_version(void) { return 1; }

RTX_API void rtx_bridge_free_string(char* p) {
  if (p) free(p);
}

static char* dup_err(const char* msg) {
  if (!msg) return nullptr;
  size_t n = strlen(msg) + 1;
  char* p = static_cast<char*>(malloc(n));
  if (p) memcpy(p, msg, n);
  return p;
}

static bool g_inited = false;
static NVSDK_NGX_Parameter* g_params = nullptr;
static CUcontext g_ctx = nullptr;
static cudaStream_t g_stream = nullptr;

static NVSDK_NGX_Result check_ngx(NVSDK_NGX_Result r, const char* what, std::string& err) {
  if (NVSDK_NGX_SUCCEED(r)) return r;
  err = std::string(what) + " failed";
  return r;
}

static int ensure_init(std::string& err) {
  if (g_inited) return 0;
  CUresult cr = cuInit(0);
  if (cr != CUDA_SUCCESS) {
    err = "cuInit failed";
    return -1;
  }
  CUdevice dev = 0;
  cr = cuDeviceGet(&dev, 0);
  if (cr != CUDA_SUCCESS) {
    err = "cuDeviceGet failed";
    return -1;
  }
  cr = cuCtxCreate(&g_ctx, 0, dev);
  if (cr != CUDA_SUCCESS) {
    err = "cuCtxCreate failed";
    return -1;
  }
  if (cudaStreamCreate(&g_stream) != cudaSuccess) {
    err = "cudaStreamCreate failed";
    return -1;
  }
  // 应用数据目录：当前模块旁
  wchar_t path[MAX_PATH]{};
  GetModuleFileNameW(nullptr, path, MAX_PATH);
  // 与 TouchDesigner NVIDIARTXVideoTOP 一致：应用目录 + 回退当前目录
  auto r = NVSDK_NGX_CUDA_Init(0, path);
  if (!NVSDK_NGX_SUCCEED(r)) {
    r = NVSDK_NGX_CUDA_Init(0, L".");
  }
  if (!NVSDK_NGX_SUCCEED(r)) {
    err = "NVSDK_NGX_CUDA_Init failed (需要 RTX GPU + 较新驱动 + SDK 运行时 DLL)";
    return -2;
  }
  r = NVSDK_NGX_CUDA_GetCapabilityParameters(&g_params);
  if (!NVSDK_NGX_SUCCEED(r) || !g_params) {
    err = "NVSDK_NGX_CUDA_GetCapabilityParameters failed";
    return -2;
  }
  g_inited = true;
  return 0;
}

RTX_API int rtx_bridge_probe(char** err_out) {
  if (err_out) *err_out = nullptr;
  std::string err;
  int rc = ensure_init(err);
  if (rc != 0) {
    if (err_out) *err_out = dup_err(err.c_str());
    return 0;
  }
  int thdr = 0, vsr = 0;
  g_params->Get(NVSDK_NGX_Parameter_TrueHDR_Available, &thdr);
  g_params->Get(NVSDK_NGX_Parameter_VSR_Available, &vsr);
  if (!thdr && !vsr) {
    if (err_out) *err_out = dup_err("TrueHDR/VSR 在此 GPU 上不可用");
    return 0;
  }
  return 1;
}

static NVSDK_NGX_VSR_QualityLevel map_quality(int q) {
  switch (q) {
    case 0: return NVSDK_NGX_VSR_Quality_Bicubic;
    case 1: return NVSDK_NGX_VSR_Quality_Low;
    case 2: return NVSDK_NGX_VSR_Quality_Medium;
    case 3: return NVSDK_NGX_VSR_Quality_High;
    default: return NVSDK_NGX_VSR_Quality_Ultra;
  }
}

/**
 * mode: 1=THDR, 2=VSR, 3=VSR+THDR
 * vsr_scale: 1/2/4
 * 输入 rgba：in_w*in_h*4 float
 * 输出缓冲须能容纳 out_w*out_h*4；函数写回实际 out_w/out_h
 */
RTX_API int rtx_bridge_process(
    const float* rgba_in,
    uint32_t in_w,
    uint32_t in_h,
    float* rgba_out,
    uint32_t* out_w,
    uint32_t* out_h,
    int mode,
    int vsr_quality,
    uint32_t contrast,
    uint32_t saturation,
    uint32_t middle_gray,
    uint32_t max_luminance,
    uint32_t vsr_scale,
    char** err_out)
{
  if (err_out) *err_out = nullptr;
  std::string err;
  if (!rgba_in || !rgba_out || !out_w || !out_h || in_w == 0 || in_h == 0) {
    if (err_out) *err_out = dup_err("invalid arguments");
    return 1;
  }
  if (ensure_init(err) != 0) {
    if (err_out) *err_out = dup_err(err.c_str());
    return 2;
  }

  const bool do_thdr = (mode == 1 || mode == 3);
  const bool do_vsr = (mode == 2 || mode == 3);
  if (!do_thdr && !do_vsr) {
    if (err_out) *err_out = dup_err("mode must be 1/2/3");
    return 1;
  }
  if (vsr_scale != 1 && vsr_scale != 2 && vsr_scale != 4) vsr_scale = do_vsr ? 2 : 1;
  if (!do_vsr) vsr_scale = 1;

  const uint32_t ow = in_w * vsr_scale;
  const uint32_t oh = in_h * vsr_scale;
  *out_w = ow;
  *out_h = oh;

  // 上传为 8-bit RGBA CUDA array（NGX 纹理入口常见路径）
  std::vector<uint8_t> u8(static_cast<size_t>(in_w) * in_h * 4);
  for (size_t i = 0, n = u8.size() / 4; i < n; ++i) {
    for (int c = 0; c < 4; ++c) {
      float v = rgba_in[i * 4 + c];
      if (c < 3) v = v < 0.f ? 0.f : (v > 1.f ? 1.f : v);
      u8[i * 4 + c] = static_cast<uint8_t>(v * 255.f + 0.5f);
    }
  }

  CUarray in_array = nullptr;
  CUDA_ARRAY_DESCRIPTOR ad{};
  ad.Width = in_w;
  ad.Height = in_h;
  ad.Format = CU_AD_FORMAT_UNSIGNED_INT8;
  ad.NumChannels = 4;
  if (cuArrayCreate(&in_array, &ad) != CUDA_SUCCESS) {
    if (err_out) *err_out = dup_err("cuArrayCreate input failed");
    return 3;
  }
  CUDA_MEMCPY2D copy{};
  copy.srcMemoryType = CU_MEMORYTYPE_HOST;
  copy.srcHost = u8.data();
  copy.srcPitch = in_w * 4;
  copy.dstMemoryType = CU_MEMORYTYPE_ARRAY;
  copy.dstArray = in_array;
  copy.WidthInBytes = in_w * 4;
  copy.Height = in_h;
  if (cuMemcpy2D(&copy) != CUDA_SUCCESS) {
    cuArrayDestroy(in_array);
    if (err_out) *err_out = dup_err("cuMemcpy2D input failed");
    return 3;
  }

  CUtexObject in_tex = 0;
  CUDA_RESOURCE_DESC rd{};
  rd.resType = CU_RESOURCE_TYPE_ARRAY;
  rd.res.array.hArray = in_array;
  CUDA_TEXTURE_DESC td{};
  td.addressMode[0] = td.addressMode[1] = td.addressMode[2] = CU_TR_ADDRESS_MODE_CLAMP;
  td.filterMode = CU_TR_FILTER_MODE_LINEAR;
  td.flags = CU_TRSF_NORMALIZED_COORDINATES;
  if (cuTexObjectCreate(&in_tex, &rd, &td, nullptr) != CUDA_SUCCESS) {
    cuArrayDestroy(in_array);
    if (err_out) *err_out = dup_err("cuTexObjectCreate failed");
    return 3;
  }

  // 输出：FP16 RGBA（scRGB）
  CUarray out_array = nullptr;
  CUDA_ARRAY_DESCRIPTOR oad{};
  oad.Width = ow;
  oad.Height = oh;
  oad.Format = CU_AD_FORMAT_HALF;
  oad.NumChannels = 4;
  if (cuArrayCreate(&out_array, &oad) != CUDA_SUCCESS) {
    cuTexObjectDestroy(in_tex);
    cuArrayDestroy(in_array);
    if (err_out) *err_out = dup_err("cuArrayCreate output failed");
    return 3;
  }
  CUsurfObject out_surf = 0;
  CUDA_RESOURCE_DESC ord{};
  ord.resType = CU_RESOURCE_TYPE_ARRAY;
  ord.res.array.hArray = out_array;
  if (cuSurfObjectCreate(&out_surf, &ord) != CUDA_SUCCESS) {
    cuArrayDestroy(out_array);
    cuTexObjectDestroy(in_tex);
    cuArrayDestroy(in_array);
    if (err_out) *err_out = dup_err("cuSurfObjectCreate failed");
    return 3;
  }

  NVSDK_NGX_Handle* thdr = nullptr;
  NVSDK_NGX_Handle* vsr = nullptr;
  int rc = 0;

  auto cleanup = [&]() {
    if (thdr) NVSDK_NGX_CUDA_ReleaseFeature(thdr);
    if (vsr) NVSDK_NGX_CUDA_ReleaseFeature(vsr);
    cuSurfObjectDestroy(out_surf);
    cuArrayDestroy(out_array);
    cuTexObjectDestroy(in_tex);
    cuArrayDestroy(in_array);
  };

  if (do_thdr) {
    int avail = 0;
    g_params->Get(NVSDK_NGX_Parameter_TrueHDR_Available, &avail);
    if (!avail) {
      cleanup();
      if (err_out) *err_out = dup_err("TrueHDR unavailable");
      return 4;
    }
    NVSDK_NGX_CUDA_TRUEHDR_Create_Params cp{};
    cp.InCUContext = reinterpret_cast<void*>(g_ctx);
    cp.InCUStream = reinterpret_cast<void*>(g_stream);
    auto r = NGX_CUDA_CREATE_TRUEHDR(&thdr, g_params, &cp);
    if (!NVSDK_NGX_SUCCEED(r)) {
      cleanup();
      if (err_out) *err_out = dup_err("NGX_CUDA_CREATE_TRUEHDR failed");
      return 4;
    }
  }
  if (do_vsr) {
    int avail = 0;
    g_params->Get(NVSDK_NGX_Parameter_VSR_Available, &avail);
    if (!avail) {
      cleanup();
      if (err_out) *err_out = dup_err("VSR unavailable");
      return 4;
    }
    NVSDK_NGX_CUDA_VSR_Create_Params cp{};
    cp.InCUContext = reinterpret_cast<void*>(g_ctx);
    cp.InCUStream = reinterpret_cast<void*>(g_stream);
    auto r = NGX_CUDA_CREATE_VSR(&vsr, g_params, &cp);
    if (!NVSDK_NGX_SUCCEED(r)) {
      cleanup();
      if (err_out) *err_out = dup_err("NGX_CUDA_CREATE_VSR failed");
      return 4;
    }
  }

  // 中间缓冲（VSR→THDR）
  CUarray mid_array = nullptr;
  CUtexObject mid_tex = 0;
  CUsurfObject mid_surf = 0;
  const bool need_mid = do_vsr && do_thdr;
  if (need_mid) {
    CUDA_ARRAY_DESCRIPTOR mad{};
    mad.Width = ow;
    mad.Height = oh;
    mad.Format = CU_AD_FORMAT_UNSIGNED_INT8;
    mad.NumChannels = 4;
    if (cuArrayCreate(&mid_array, &mad) != CUDA_SUCCESS) {
      cleanup();
      if (err_out) *err_out = dup_err("cuArrayCreate mid failed");
      return 3;
    }
    CUDA_RESOURCE_DESC mrd{};
    mrd.resType = CU_RESOURCE_TYPE_ARRAY;
    mrd.res.array.hArray = mid_array;
    CUDA_TEXTURE_DESC mtd{};
    mtd.addressMode[0] = mtd.addressMode[1] = mtd.addressMode[2] = CU_TR_ADDRESS_MODE_CLAMP;
    mtd.filterMode = CU_TR_FILTER_MODE_LINEAR;
    mtd.flags = CU_TRSF_NORMALIZED_COORDINATES;
    cuTexObjectCreate(&mid_tex, &mrd, &mtd, nullptr);
    cuSurfObjectCreate(&mid_surf, &mrd);
  }

  uint64_t in_tex_u = static_cast<uint64_t>(in_tex);
  if (do_vsr) {
    NVSDK_NGX_CUDA_VSR_Eval_Params ep{};
    ep.pInput = &in_tex_u;
    ep.pOutput = need_mid ? &mid_surf : reinterpret_cast<CUsurfObject*>(&out_surf);
    ep.InputSubrectBase.X = 0;
    ep.InputSubrectBase.Y = 0;
    ep.InputSubrectSize.Width = in_w;
    ep.InputSubrectSize.Height = in_h;
    ep.OutputSubrectBase.X = 0;
    ep.OutputSubrectBase.Y = 0;
    ep.OutputSubrectSize.Width = ow;
    ep.OutputSubrectSize.Height = oh;
    ep.QualityLevel = map_quality(vsr_quality);
    auto r = NGX_CUDA_EVALUATE_VSR(vsr, g_params, &ep);
    if (!NVSDK_NGX_SUCCEED(r)) {
      if (mid_surf) cuSurfObjectDestroy(mid_surf);
      if (mid_tex) cuTexObjectDestroy(mid_tex);
      if (mid_array) cuArrayDestroy(mid_array);
      cleanup();
      if (err_out) *err_out = dup_err("NGX_CUDA_EVALUATE_VSR failed");
      return 5;
    }
  }

  if (do_thdr) {
    NVSDK_NGX_CUDA_TRUEHDR_Eval_Params ep{};
    uint64_t thdr_in = need_mid ? static_cast<uint64_t>(mid_tex) : in_tex_u;
    ep.pInput = &thdr_in;
    ep.pOutput = &out_surf;
    ep.InputSubrectTL.X = 0;
    ep.InputSubrectTL.Y = 0;
    ep.InputSubrectBR.Width = need_mid ? ow : in_w;
    ep.InputSubrectBR.Height = need_mid ? oh : in_h;
    ep.OutputSubrectTL.X = 0;
    ep.OutputSubrectTL.Y = 0;
    ep.OutputSubrectBR.Width = ow;
    ep.OutputSubrectBR.Height = oh;
    ep.Contrast = contrast;
    ep.Saturation = saturation;
    ep.MiddleGray = middle_gray;
    ep.MaxLuminance = max_luminance;
    auto r = NGX_CUDA_EVALUATE_TRUEHDR(thdr, g_params, &ep);
    if (!NVSDK_NGX_SUCCEED(r)) {
      if (mid_surf) cuSurfObjectDestroy(mid_surf);
      if (mid_tex) cuTexObjectDestroy(mid_tex);
      if (mid_array) cuArrayDestroy(mid_array);
      cleanup();
      if (err_out) *err_out = dup_err("NGX_CUDA_EVALUATE_TRUEHDR failed");
      return 5;
    }
  }

  cudaDeviceSynchronize();

  // 下载 FP16 → float32
  const size_t pix = static_cast<size_t>(ow) * oh;
  std::vector<uint16_t> halfs(pix * 4);
  CUDA_MEMCPY2D dcopy{};
  dcopy.srcMemoryType = CU_MEMORYTYPE_ARRAY;
  dcopy.srcArray = out_array;
  dcopy.dstMemoryType = CU_MEMORYTYPE_HOST;
  dcopy.dstHost = halfs.data();
  dcopy.dstPitch = ow * 4 * sizeof(uint16_t);
  dcopy.WidthInBytes = ow * 4 * sizeof(uint16_t);
  dcopy.Height = oh;
  if (cuMemcpy2D(&dcopy) != CUDA_SUCCESS) {
    if (mid_surf) cuSurfObjectDestroy(mid_surf);
    if (mid_tex) cuTexObjectDestroy(mid_tex);
    if (mid_array) cuArrayDestroy(mid_array);
    cleanup();
    if (err_out) *err_out = dup_err("cuMemcpy2D download failed");
    return 3;
  }

  // half → float（简易位转换）
  auto half_to_float = [](uint16_t h) -> float {
    uint32_t sign = (h >> 15) & 1;
    uint32_t exp = (h >> 10) & 0x1f;
    uint32_t mant = h & 0x3ff;
    uint32_t f;
    if (exp == 0) {
      if (mant == 0) f = sign << 31;
      else {
        exp = 127 - 15 + 1;
        while ((mant & 0x400) == 0) { mant <<= 1; --exp; }
        mant &= 0x3ff;
        f = (sign << 31) | (exp << 23) | (mant << 13);
      }
    } else if (exp == 31) {
      f = (sign << 31) | 0x7f800000 | (mant << 13);
    } else {
      f = (sign << 31) | ((exp + (127 - 15)) << 23) | (mant << 13);
    }
    float out;
    memcpy(&out, &f, sizeof(out));
    return out;
  };

  if (do_thdr) {
    for (size_t i = 0; i < pix * 4; ++i)
      rgba_out[i] = half_to_float(halfs[i]);
  } else {
    // 仅 VSR：输出仍是 8-bit 归一化路径时可能是 UNORM；若为 half 则按 [0,1] 解释
    for (size_t i = 0; i < pix * 4; ++i) {
      float v = half_to_float(halfs[i]);
      rgba_out[i] = v;
    }
  }

  if (mid_surf) cuSurfObjectDestroy(mid_surf);
  if (mid_tex) cuTexObjectDestroy(mid_tex);
  if (mid_array) cuArrayDestroy(mid_array);
  cleanup();
  return 0;
}

}  // extern "C"
