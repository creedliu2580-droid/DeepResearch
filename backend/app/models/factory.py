"""
模型工厂 — 集中管理所有 LLM 实例创建。

改造点（2026-10-29）：
  以前：单一 base_url + api_key（来自 .env），AVAILABLE_MODELS 只是名字列表
  现在：按 model_name 找 provider → 取该 provider 的 base_url + api_key
        支持多厂商混合、YAML 声明式配置

业务代码只从这里拿模型，不直接依赖 langchain-openai 的 ChatOpenAI。
换模型供应商（通义/智谱/Ollama）只需：
  1. llm.yaml 里加一个 provider + models 条目
  2. 若协议非 OpenAI 兼容（Anthropic 原生 API），再加一个 Provider 实现类
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ..config.loader import ModelRegistry, load_model_registry, Role
from ..settings import get_settings


class ModelFactory:
    """轻量工厂 — 根据 (role, model_name) 返回已配置的 ChatModel 实例。

    缓存键是三元组 (role, model_name, provider_name)，同一模型跨角色复用会走
    不同缓存 entry（仅 temperature 等参数不同），但底层 HTTP client 是同一个。
    """

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self._registry = registry or load_model_registry()
        self._instances: dict[tuple[str, str, str], BaseChatModel] = {}

    # ---- 对外 API -----------------------------------------------------------

    def get(self, role: Role, model: str | None = None, streaming: bool = True) -> BaseChatModel:
        """取角色对应的 LLM 实例。

        Args:
            role: 业务角色（plan / search / write / review），只影响 temperature
            model: 模型名（前端所选）。None 时按角色默认值回退：
                   role_defaults[role] → registry.default_model
            streaming: 是否流式输出。内部判断类调用传 False，避免把中间 JSON 流到前端。
        """
        model_name = (model or "").strip() or self._registry.resolve_model_for_role(role)
        if not model_name:
            raise RuntimeError(
                "无可用模型名：请在 llm.yaml 里配置 models 清单，"
                "或在 .env 里写 AVAILABLE_MODELS / DEFAULT_MODEL。"
            )

        entry = self._registry.get_model_entry(model_name)
        provider = self._registry.get_provider(entry.provider)

        key = (role, model_name, provider.name, streaming)
        if key not in self._instances:
            self._instances[key] = self._make_llm(role, model_name, provider, streaming=streaming)
        return self._instances[key]

    def list_available(self) -> list[dict]:
        """返回角色 → 模型的映射，给 /api/models 接口用。"""
        return [
            {
                "role": r,
                "model": self._registry.resolve_model_for_role(r)
                or self._registry.default_model,
            }
            for r in ("plan", "search", "write", "review")
        ]

    def list_candidates(self) -> list[str]:
        """返回前端可选的模型清单（来自 llm.yaml models 字段）。"""
        return self._registry.list_candidates()

    def registry(self) -> ModelRegistry:
        return self._registry

    # ---- 内部 ---------------------------------------------------------------

    @staticmethod
    def _make_llm(
        role: Role,
        model_name: str,
        provider,
        streaming: bool = True,
    ) -> BaseChatModel:
        # 按 provider 的 base_url 选实现 —— 目前全是 OpenAI 兼容端点
        # 将来要接入 Anthropic 原生 / Google Gemini / 本地 Ollama，
        # 在这里按 provider 类型分派：
        #   if provider.type == "anthropic":
        #       return ChatAnthropic(...)
        #   elif provider.type == "google":
        #       return ChatGoogleGenerativeAI(...)
        #   else:
        #       return ChatOpenAI(...)
        if role == "review":
            temp = 0.2
        elif role == "write":
            temp = 0.3  # 事实性写作用低温度，减少编造具体数字
        else:
            temp = 0.7
        settings = get_settings()
        return ChatOpenAI(
            model=model_name,
            base_url=provider.base_url,
            api_key=provider.api_key,
            temperature=temp,
            streaming=streaming,
            timeout=settings.llm.timeout_seconds,
            max_retries=settings.llm.max_retries,
        )


@lru_cache(maxsize=1)
def get_model_factory() -> ModelFactory:
    return ModelFactory()
