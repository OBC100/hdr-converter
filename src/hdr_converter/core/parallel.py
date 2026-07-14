"""线程 / 进程并行辅助（JPG 导出等）。"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


def pipeline_workers() -> int:
    """单张图内 CPU 密集型并行任务上限。"""
    return min(2, os.cpu_count() or 1)


def batch_workers(file_count: int) -> int:
    """多文件批处理进程数。"""
    if file_count <= 1:
        return 1
    return min(file_count, os.cpu_count() or 1)


def avif_encode_numthreads(*, parallel_jobs: int = 1) -> int:
    """libavif 每路编码线程数：单路用满 CPU，多路并行时按 job 数平分。"""
    cpus = os.cpu_count() or 4
    return max(1, cpus // max(1, parallel_jobs))


def run_parallel_pair(
    first,
    second,
    *,
    max_workers: int | None = None,
) -> tuple[T, T]:
    """并行执行两个无依赖可调用对象。"""
    workers = max_workers or pipeline_workers()
    if workers < 2:
        return first(), second()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(first)
        f2 = ex.submit(second)
        return f1.result(), f2.result()


def limit_blas_threads_in_child() -> None:
    """子进程内限制 BLAS 线程，避免与多进程叠乘占满 CPU。"""
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(key, "1")
