"""进程内并发限流信号量。"""
from __future__ import annotations

import asyncio

from .settings import get_settings

_llm_sem: asyncio.Semaphore | None = None
_search_sem: asyncio.Semaphore | None = None


def get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_sem
    if _llm_sem is None:
        _llm_sem = asyncio.Semaphore(get_settings().concurrency.max_concurrent_llm)
    return _llm_sem


def get_search_semaphore() -> asyncio.Semaphore:
    global _search_sem
    if _search_sem is None:
        _search_sem = asyncio.Semaphore(get_settings().concurrency.max_concurrent_search)
    return _search_sem
