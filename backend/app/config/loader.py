"""
LLM 多厂商配置加载器 —— 读 llm.yaml，解析 ${ENV_VAR} 占位，提供类型安全访问。

数据契约（YAML → Python 数据类）：
  llm.yaml
  ├── providers:   dict[str, {base_url, api_key, default_model?}]
  ├── models:      list[{name, provider, display?}]
  ├── role_defaults:   dict[role: str]      # 空字符串表示用 default_model
  └── default_model:   str                  # 全局回退

环境变量占位解析：
  ${VAR}           → os.environ[VAR]，缺失则抛明确错误（密钥类）或留空
  ${VAR:default}   → os.environ[VAR] 或 default

所有敏感字段（api_key）必须用 ${ENV} 占位，禁止硬编码。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

logger = logging.getLogger("config.loader")

Role = Literal["plan", "search", "write", "review"]

# 支持两种位置：backend/config/llm.yaml（推荐）或 backend/llm.yaml（兼容）
_YAML_CANDIDATES = [
    Path(__file__).resolve().parent / "llm.yaml",           # backend/config/llm.yaml
    Path(__file__).resolve().parent.parent / "llm.yaml",    # backend/llm.yaml
]

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::([^}]*))?\}")


@dataclass
class ProviderEntry:
    """单个厂商的连接信息。"""
    name: str
    base_url: str
    api_key: str
    default_model: str = ""


@dataclass
class ModelEntry:
    """单个模型 —— 绑定到某个 provider。"""
    name: str
    provider: str
    display: str = ""

    @property
    def label(self) -> str:
        return self.display or self.name


@dataclass
class ModelRegistry:
    """完整的模型注册表 —— 工厂类从中取 LLM 实例。"""
    providers: dict[str, ProviderEntry] = field(default_factory=dict)
    models: dict[str, ModelEntry] = field(default_factory=dict)  # model_name → entry
    role_defaults: dict[str, str] = field(default_factory=dict)  # role → model_name
    default_model: str = ""

    # ---- 查询接口 -----------------------------------------------------------

    def get_model_entry(self, model_name: str) -> ModelEntry:
        entry = self.models.get(model_name)
        if not entry:
            available = ", ".join(sorted(self.models.keys()))
            raise KeyError(
                f"未找到模型 '{model_name}'。YAML 中已配置的模型：{available}"
            )
        return entry

    def get_provider(self, provider_name: str) -> ProviderEntry:
        p = self.providers.get(provider_name)
        if not p:
            available = ", ".join(sorted(self.providers.keys()))
            raise KeyError(
                f"未找到 provider '{provider_name}'。YAML 中已配置的 provider：{available}"
            )
        return p

    def resolve_model_for_role(self, role: Role) -> str:
        """某角色未指定 model_override 时 → role_defaults → default_model。"""
        return self.role_defaults.get(role) or self.default_model

    def list_candidates(self) -> list[str]:
        """前端下拉候选列表 —— 所有已注册模型的 name。"""
        return list(self.models.keys())


# ---------------------------------------------------------------------------
# 实现细节
# ---------------------------------------------------------------------------

def _resolve_env(value: object) -> object:
    """递归解析 str 中的 ${VAR} / ${VAR:default}。"""
    if isinstance(value, str):
        def replacer(match: re.Match) -> str:
            var = match.group(1)
            default = match.group(2)
            if var in os.environ:
                return os.environ[var]
            if default is not None:
                return default
            # 密钥类字段缺失 → 留空，调用方创建 LLM 实例时会收到 auth 错误，
            # 避免把“加载配置”变成启动阻塞（某些 provider 暂时不启用但 YAML 里写了也没关系）
            logger.warning("环境变量 %s 未设置，对应字段将为空字符串", var)
            return ""

        return _ENV_PATTERN.sub(replacer, value)

    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _find_yaml_file() -> Path | None:
    for p in _YAML_CANDIDATES:
        if p.exists():
            return p
    return None


def _validate(model_name: str, registry: ModelRegistry) -> None:
    """加载后校验 —— 每个 model 的 provider 必须存在。"""
    missing = []
    for entry in registry.models.values():
        if entry.provider not in registry.providers:
            missing.append(f"模型 {entry.name} 引用了未定义的 provider {entry.provider}")
    if missing:
        raise ValueError(
            "llm.yaml 配置错误：\n  " + "\n  ".join(missing)
        )

    # default_model 必须在 models 里（若填写了）
    if registry.default_model and registry.default_model not in registry.models:
        candidates = ", ".join(sorted(registry.models.keys()))
        raise ValueError(
            f"llm.yaml 的 default_model='{registry.default_model}' 不在 models 清单中。"
            f"已注册模型：{candidates}"
        )


def _fallback_from_env() -> ModelRegistry | None:
    """YAML 缺失或 models/providers 为空时，用 .env 里的老字段构造一份保底 registry。

    这让老用户可以零成本迁移：不配 llm.yaml 也能跑。
    """
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    api_key = os.environ.get("LLM_API_KEY", "")
    default_model = os.environ.get("DEFAULT_MODEL", "deepseek-chat")
    available_raw = os.environ.get(
        "AVAILABLE_MODELS", "deepseek-flash,deepseek-chat,deepseek-reasoner"
    )

    # 判断 .env 里是否有可用值 —— base_url 里没有 api_key 占位且 api_key 非空才认为是有效配置
    if not api_key or api_key.startswith("sk-xxx") or api_key == "sk-xxx":
        return None

    provider_name = "default"
    provider = ProviderEntry(
        name=provider_name, base_url=base_url, api_key=api_key, default_model=default_model
    )

    registry = ModelRegistry(
        providers={provider_name: provider},
        default_model=default_model,
    )

    for name in [s.strip() for s in available_raw.split(",") if s.strip()]:
        registry.models[name] = ModelEntry(name=name, provider=provider_name)

    # 角色专用模型（老 .env 里的 PLAN_MODEL 等）
    for role in ("plan", "search", "write", "review"):
        role_key = role.upper() + "_MODEL"
        role_val = os.environ.get(role_key, "").strip()
        if role_val:
            # 若老配置指定了新模型名，把它也注册进来
            if role_val not in registry.models:
                registry.models[role_val] = ModelEntry(name=role_val, provider=provider_name)
            registry.role_defaults[role] = role_val

    return registry


@lru_cache(maxsize=1)
def load_model_registry() -> ModelRegistry:
    """加载 llm.yaml；若不存在或内容为空则回退到 .env 老配置。

    缓存单例 —— 多线程安全；想热刷新可清 cache 后重调。
    """
    yaml_path = _find_yaml_file()
    if yaml_path:
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # 用 .env 里的 LLM_API_KEY / LLM_BASE_URL 作为 ${VAR} 占位解析来源
        resolved = _resolve_env(raw)

        providers_raw = resolved.get("providers") or {}
        models_raw = resolved.get("models") or []

        if providers_raw and models_raw:
            providers = {
                name: ProviderEntry(
                    name=name,
                    base_url=p.get("base_url", ""),
                    api_key=p.get("api_key", ""),
                    default_model=p.get("default_model", ""),
                )
                for name, p in providers_raw.items()
            }
            models = {
                m["name"]: ModelEntry(
                    name=m["name"],
                    provider=m.get("provider", ""),
                    display=m.get("display", ""),
                )
                for m in models_raw
                if m.get("name")
            }
            role_defaults = resolved.get("role_defaults") or {}
            default_model = resolved.get("default_model", "") or ""

            registry = ModelRegistry(
                providers=providers,
                models=models,
                role_defaults={r: (v or "") for r, v in role_defaults.items()},
                default_model=default_model,
            )
            _validate(default_model, registry)
            logger.info("已从 %s 加载 %d 个 provider / %d 个模型", yaml_path, len(providers), len(models))
            return registry

    # 回退：用 .env 里的老字段构造一份 registry
    fallback = _fallback_from_env()
    if fallback is not None and fallback.models:
        logger.info("未找到可用 llm.yaml，使用 .env 老配置构造 registry（%d 个模型）", len(fallback.models))
        return fallback

    # 最后保底 —— 一个最小可用的 registry，让错误在真正调用 LLM 时才暴露
    logger.warning("未找到有效模型配置，registry 为空。调用 LLM 时会报密钥缺失。")
    return ModelRegistry(default_model="")


def invalidate_registry_cache() -> None:
    """清缓存 —— 改了 llm.yaml 后想热生效，调一下再重调 load_model_registry()。"""
    load_model_registry.cache_clear()
