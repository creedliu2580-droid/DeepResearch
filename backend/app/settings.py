"""
集中配置模块 — 所有可参数化项都收敛于此，修改不需要动业务代码。

LLM 模型配置：已分离到 config/llm.yaml（多厂商 providers + models 声明）
             通过 config.loader.load_model_registry() 访问，.env 只保留密钥占位。
研究深度 / Tavily / 写作 / 服务端口：继续从 .env 读取。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


EffortLevel = Literal["low", "medium", "high"]

# .env 位于 backend/ 目录（app/ 包的上一级），用绝对路径避免依赖启动目录
_BASE_DIR = Path(__file__).resolve().parent.parent
_ENV = SettingsConfigDict(env_file=str(_BASE_DIR / ".env"), extra="ignore")


class EffortConfig(BaseSettings):
    """研究深度参数 — 初始化查询数 & 最大反思轮次。"""

    low_initial_queries: int = Field(1, validation_alias="EFFORT_LOW_INITIAL_QUERIES")
    low_max_rounds: int = Field(1, validation_alias="EFFORT_LOW_MAX_ROUNDS")
    medium_initial_queries: int = Field(3, validation_alias="EFFORT_MEDIUM_INITIAL_QUERIES")
    medium_max_rounds: int = Field(3, validation_alias="EFFORT_MEDIUM_MAX_ROUNDS")
    high_initial_queries: int = Field(5, validation_alias="EFFORT_HIGH_INITIAL_QUERIES")
    high_max_rounds: int = Field(5, validation_alias="EFFORT_HIGH_MAX_ROUNDS")

    model_config = _ENV

    def get(self, level: EffortLevel) -> dict:
        return {
            "initial_queries": getattr(self, f"{level}_initial_queries"),
            "max_rounds": getattr(self, f"{level}_max_rounds"),
        }


class TavilyConfig(BaseSettings):
    api_key: str = Field("tvly-xxx", validation_alias="TAVILY_API_KEY")
    max_results: int = Field(3, validation_alias="TAVILY_MAX_RESULTS")
    search_depth: str = Field("advanced", validation_alias="TAVILY_SEARCH_DEPTH")

    model_config = _ENV


class WritingConfig(BaseSettings):
    """写作子图参数。"""

    max_revisions: int = Field(2, validation_alias="WRITING_MAX_REVISIONS")
    pass_score: float = Field(6.0, validation_alias="WRITING_PASS_SCORE")

    model_config = _ENV


class LlmConfig(BaseSettings):
    """LLM 调用可靠性参数。"""

    timeout_seconds: float = Field(120.0, validation_alias="LLM_TIMEOUT_SECONDS")
    max_retries: int = Field(2, validation_alias="LLM_MAX_RETRIES")
    fallback_models_raw: str = Field("", validation_alias="LLM_FALLBACK_MODELS")

    model_config = _ENV

    @property
    def fallback_models(self) -> list[str]:
        """主模型失败后依次尝试的兜底模型名（逗号分隔）。空 = 无 failover。"""
        return [s.strip() for s in self.fallback_models_raw.split(",") if s.strip()]


class ConcurrencyConfig(BaseSettings):
    """并发 / 限流参数。"""

    max_concurrent_llm: int = Field(4, validation_alias="MAX_CONCURRENT_LLM")
    max_concurrent_search: int = Field(4, validation_alias="MAX_CONCURRENT_SEARCH")
    rate_limit_per_minute: int = Field(60, validation_alias="RATE_LIMIT_PER_MINUTE")

    model_config = _ENV


class ServerConfig(BaseSettings):
    host: str = Field("0.0.0.0", validation_alias="SERVER_HOST")
    port: int = Field(8010, validation_alias="SERVER_PORT")
    checkpoint_db_path: str = Field(
        str(_BASE_DIR / "checkpoints.sqlite"),
        validation_alias="CHECKPOINT_DB_PATH",
    )
    cors_origins_raw: str = Field(
        "http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="CORS_ORIGINS",
    )

    model_config = _ENV

    @property
    def cors_origins(self) -> list[str]:
        """逗号分隔字符串 → list。"""
        return [s.strip() for s in self.cors_origins_raw.split(",") if s.strip()]


class Settings(BaseSettings):
    """全局配置 — 聚合所有子配置，通过 lru_cache 做单例。"""

    effort: EffortConfig = EffortConfig()
    tavily: TavilyConfig = TavilyConfig()
    writing: WritingConfig = WritingConfig()
    llm: LlmConfig = LlmConfig()
    concurrency: ConcurrencyConfig = ConcurrencyConfig()
    server: ServerConfig = ServerConfig()
    model_config = _ENV

    @staticmethod
    def effort_note(level: EffortLevel, role: str) -> str:
        """针对不同研究深度，给 prompt 注入一段引导性说明。

        role: plan / split_queries / reflect —— 不同节点的引导维度不同。
        """
        notes: dict[str, dict[str, str]] = {
            "plan": {
                "low": "**聚焦策略**：只列出 2-3 个最核心的子问题，信源优先行业报告和官方文档，分析框架选最简单的一种（如时间线或分类矩阵）。不要试图覆盖所有维度。",
                "medium": "**平衡策略**：列出 3-5 个核心子问题，覆盖主要维度但不追求穷尽。信源兼顾学术论文、行业报告和新闻，分析框架选 2 种互补的。",
                "high": "**全面策略**：列出 5-7 个子问题，覆盖市场、玩家、技术、定价、趋势等多个维度。信源要多样化（学术/行业/官方/新闻/博客），分析框架尽量完整（如波特五力+SWOT 组合）。",
            },
            "split_queries": {
                "low": "**1 个宽泛查询**：只需 1 个覆盖核心主题的查询，不需要细拆。直接把研究主题本身作为查询即可。",
                "medium": "**3 个互补查询**：3 个查询分别覆盖不同维度（如市场规模/主要厂商/技术趋势），语义不能重叠，每个查询要具体到可以直接喂搜索引擎。",
                "high": "**5+ 个精细查询**：5 个查询分别覆盖不同维度（如市场规模/主要厂商/定价策略/技术趋势/用户画像/融资动态），每个查询要非常具体（如带年份、带细分领域），避免模糊表述。",
            },
            "reflect": {
                "low": "**宽松评估标准**：在低深度模式下，信息基本够用即可停止反思。如果已收集到能回答核心子问题的资料，就不要再补搜。对数据冲突或边缘维度不必强求。",
                "medium": "**中等评估标准**：检查核心子问题是否都有资料支撑。如果某一维度明显缺失或数据存在冲突，应该补搜 1-2 个查询；否则可以停止。",
                "high": "**严格评估标准**：每个维度都应有硬数据支撑。模糊定性描述、多来源数据冲突、重要维度完全缺失，都应该触发补搜。不要轻易判定信息充足。",
            },
        }
        return notes.get(role, {}).get(level, "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
