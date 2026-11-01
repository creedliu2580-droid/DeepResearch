"""LLM 调用封装：超时 + 供应商 failover + 并发限流 + 指标采集。

业务代码统一通过 `invoke_llm` 调 LLM，不再直接 `factory.get(...).ainvoke(...)`，
从而集中获得：超时控制、主/备模型故障切换、并发信号量限流、调用指标。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .limits import get_llm_semaphore
from .models import get_model_factory
from .observability import record_llm
from .settings import get_settings

logger = logging.getLogger("llm")


async def invoke_llm(
    role: str,
    model_override: str | None,
    prompt: str,
    *,
    streaming: bool = True,
) -> Any:
    """按「主模型 → 兜底模型」顺序调用，带超时，失败逐级 failover。

    Args:
        role: 业务角色（plan/search/write/review）
        model_override: 前端所选模型名（空 → 角色默认模型）
        prompt: 完整 prompt 文本
        streaming: 是否流式（内部判断类调用传 False，避免中间 JSON 流到前端）
    """
    factory = get_model_factory()
    settings = get_settings()
    t0 = time.monotonic()

    primary = (model_override or "").strip()
    chain: list[str] = [primary]
    for m in settings.llm.fallback_models:
        if m not in chain:
            chain.append(m)

    errors: list[str] = []
    async with get_llm_semaphore():
        for m in chain:
            label = m or "default"
            try:
                llm = factory.get(role, m, streaming=streaming)
                result = await asyncio.wait_for(
                    llm.ainvoke(prompt),
                    timeout=settings.llm.timeout_seconds,
                )
                dur = time.monotonic() - t0
                record_llm(role, True, dur)
                logger.info("LLM 耗时 %s/%s %.1fs", role, label, dur)
                return result
            except asyncio.TimeoutError:
                msg = f"超时（>{settings.llm.timeout_seconds:.0f}s）"
                errors.append(f"{label}: {msg}")
                logger.warning("LLM 调用超时（%s/%s）: %s", role, label, msg)
            except Exception as exc:  # noqa: BLE001 - 逐级 failover，捕获所有异常
                errors.append(f"{label}: {exc}")
                logger.warning("LLM 调用失败（%s/%s）: %s", role, label, exc)

    record_llm(role, False, time.monotonic() - t0)
    raise RuntimeError("所有 LLM 调用均失败: " + " | ".join(errors))
