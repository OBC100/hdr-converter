# NVIDIA RTX Video 桥接 DLL（TrueHDR / VSR）
# 需要：NV_RTX_VIDEO_SDK、CUDA Toolkit、VS C++、CMake
cmake_minimum_required(VERSION 3.20)
project(hdr_rtx_bridge LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CUDA_STANDARD 17)

if(NOT DEFINED ENV{NV_RTX_VIDEO_SDK})
  message(FATAL_ERROR "请设置环境变量 NV_RTX_VIDEO_SDK 指向 RTX Video SDK 根目录")
endif()
set(RTX_SDK "$ENV{NV_RTX_VIDEO_SDK}")

find_package(CUDAToolkit REQUIRED)

add_library(hdr_rtx_bridge SHARED
  src/rtx_bridge.cpp
)

target_include_directories(hdr_rtx_bridge PRIVATE
  "${RTX_SDK}/include"
  ${CUDAToolkit_INCLUDE_DIRS}
)

target_link_directories(hdr_rtx_bridge PRIVATE
  "${RTX_SDK}/lib/Windows/x64"
  "${RTX_SDK}/lib/x64"
)

# 库名因 SDK 版本略有差异，按常见命名尝试
find_library(NGX_LIB
  NAMES nvsdk_ngx_d nvsdk_ngx_s nvsdk_ngx
  PATHS
    "${RTX_SDK}/lib/Windows/x64"
    "${RTX_SDK}/lib/x64"
    "${RTX_SDK}/lib"
  REQUIRED
)

target_link_libraries(hdr_rtx_bridge PRIVATE
  CUDA::cudart
  CUDA::cuda_driver
  ${NGX_LIB}
)

target_compile_definitions(hdr_rtx_bridge PRIVATE HDR_RTX_BRIDGE_EXPORTS)

set_target_properties(hdr_rtx_bridge PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
  OUTPUT_NAME "hdr_rtx_bridge"
)

# 复制 SDK 运行时 DLL（若存在）
file(GLOB NGX_DLLS
  "${RTX_SDK}/bin/Windows/x64/rel/nvngx_*.dll"
  "${RTX_SDK}/bin/x64/nvngx_*.dll"
  "${RTX_SDK}/bin/features/nvngx_*.dll"
)
foreach(dll ${NGX_DLLS})
  add_custom_command(TARGET hdr_rtx_bridge POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${dll}" $<TARGET_FILE_DIR:hdr_rtx_bridge>
  )
endforeach()
