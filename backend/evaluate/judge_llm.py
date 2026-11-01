"""评估专用的 LLM 调用通道 —— 与主程序模型配置（app/models/factory.py）解耦。

设计目标：
  评估（Judge）的模型不应受主程序「角色 → 模型」映射变化影响。主程序把 review 角色
  设成什么都好，评估可以用完全独立的模型 / 厂商 / 密钥。

配置优先级（从高到低）：
  1. evaluate_report(..., model=...) 的显式参数（对应 CLI 的 --judge-model）
  2. 环境变量 EVAL_MODEL（评估专用模型名）
  3. 环境变量 EVAL_BASE_URL + EVAL_API_KEY（覆盖连接信息，可换厂商）
  4. 兜底：主程序 review 角色的默认模型（保持向后兼容，历史行为不变）

连接信息解析：
  - 若模型名能在主 registry（llm.yaml）找到 → 复用其 provider 的连接（避免重复维护密钥）
  - 否则要求 EVAL_BASE_URL + EVAL_API_KEY 同时提供（独立 provider）

温度固定 0.2（裁判场景），不随主程序角色温度变化。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langchain_openai import ChatOpenAI

from app.config.loader import load_model_registry
from app.settings import get_settings

logger = logging.getLogger("evaluate.judge_llm")

# 裁判场景固定低温度，保证打分稳定、少随机性
EVAL_TEMPERATURE = 0.2


def resolve_eval_model(model_override: str | None = None) -> dict[str, str]:
    """解析评估模型名 + 连接信息，返回 {model, base_url, api_key}。

    与主程序 role 映射无关的独立决议链。
    """
    registry = load_model_registry()

    # 1) 显式模型名（CLI 或 EVAL_MODEL 环境变量）
    model_name = (model_override or "").strip() or os.environ.get("EVAL_MODEL", "").strip()

    if model_name:
        # 1a) 优先复用主 registry 里该模型的 provider（密钥已维护）
        entry = registry.models.get(model_name)
        if entry and entry.provider in registry.providers:
            provider = registry.providers[entry.provider]
            return {
                "model": model_name,
                "base_url": provider.base_url,
                "api_key": provider.api_key,
            }

        # 1b) 主 registry 没有 → 用独立 provider（换厂商）
        base_url = os.environ.get("EVAL_BASE_URL", "").strip()
        api_key = os.environ.get("EVAL_API_KEY", "").strip()
        if base_url and api_key:
            return {"model": model_name, "base_url": base_url, "api_key": api_key}

        available = ", ".join(sorted(registry.models.keys())) or "（空）"
        raise RuntimeError(
            f"评估模型 '{model_name}' 不在主 llm.yaml 的 models 清单中（当前：{available}），"
            "且未提供独立连接信息。请设置 EVAL_BASE_URL + EVAL_API_KEY，"
            "或把该模型加入 llm.yaml。"
        )

    # 2) 兜底：主程序 review 角色默认模型（向后兼容）
    fallback = registry.resolve_model_for_role("review") or registry.default_model
    if not fallback:
        raise RuntimeError(
            "未配置评估模型：请设置 EVAL_MODEL 环境变量，或在 llm.yaml 中配置模型清单。"
        )

    entry = registry.get_model_entry(fallback)
    provider = registry.get_provider(entry.provider)
    return {
        "model": fallback,
        "base_url": provider.base_url,
        "api_key": provider.api_key,
    }


def get_eval_model_label(model_override: str | None = None) -> str:
    """返回最终生效的模型名（用于 EvaluationResult.model 展示）。"""
    return resolve_eval_model(model_override)["model"]


async def invoke_eval_llm(
    prompt: str,
    model_override: str | None = None,
    *,
    streaming: bool = False,
) -> Any:
    """调用评估专用 LLM，返回 AIMessage。

    不经过主程序的 invoke_llm（failover/限流/指标），避免与主流程配置耦合。
    超时 / 重试沿用 settings.llm 的通用可靠性参数，但不参与主程序 role 映射。
    """
    cfg = resolve_eval_model(model_override)
    settings = get_settings()

    llm = ChatOpenAI(
        model=cfg["model"],
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        temperature=EVAL_TEMPERATURE,
        streaming=streaming,
        timeout=settings.llm.timeout_seconds,
        max_retries=settings.llm.max_retries,
    )
    return await asyncio.wait_for(
        llm.ainvoke(prompt),
        timeout=settings.llm.timeout_seconds,
    )