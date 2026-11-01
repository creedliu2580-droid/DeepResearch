"""
F8 质量评估体系（LLM-as-Judge）—— 核心评估逻辑。

独立于主流程运行：不依赖 FastAPI 生命周期 / 会话语义，仅复用 LLM 调用与 prompt 加载。
对最终报告按 PRD F8 的 5 个维度（各 1–5 分）打分，并单独做幻觉检查。

与参考项目（DeepResearch-06/eval）对齐的关键点：
  * 维度名统一为 factual_accuracy / information_coverage / logical_structure /
    timeliness / citation_quality（与 judge.md 的 JSON 字段一致）。
  * 每个维度既可返回裸数字，也可返回 {"score": N, "reason": "..."}，解析时都兼容。
  * Judge 调用带 3 次「JSON 解析失败重试」：LLM 输出无法解析时重新调用，3 次都
    失败才降级为失败结果，避免单次解析抖动静默丢分。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.prompts.loader import PromptLoader
from app.utils import parse_json

from .judge_llm import get_eval_model_label, invoke_eval_llm

logger = logging.getLogger("evaluate.judge")

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# PRD F8 定义的 5 个评估维度（顺序固定；键名与 judge.md 输出 JSON 严格一致）
DIMENSIONS: tuple[str, ...] = (
    "factual_accuracy",     # 事实准确性
    "information_coverage", # 信息覆盖度
    "logical_structure",    # 逻辑结构
    "timeliness",           # 时效性
    "citation_quality",     # 引用质量
)

# Judge 单次调用 JSON 解析失败的最大重试次数（参考项目为 3）
MAX_PARSE_RETRIES: int = 3

# PRD F8 端到端评分解读（1–5 分制）
_BANDS: tuple[tuple[float, float, str], ...] = (
    (4.0, 5.0, "优秀，接近人工撰写"),
    (3.0, 3.999999, "合格，可用但可改进"),
    (2.0, 2.999999, "较差，需调整配置"),
    (0.0, 1.999999, "不可用"),
)


def interpret_band(total: float) -> str:
    """按 PRD F8 表格把端到端总分映射为质量档位。"""
    for lo, hi, label in _BANDS:
        if lo <= total <= hi:
            return label
    return "不可用"


def clamp_score(v: float) -> float:
    """把单维分数规范化。

    0 视为「维度缺失」，保持 0（会被平均进总分，向下惩罚）；
    其余分数夹到 1–5，防止 LLM 偶尔输出越界值。
    """
    if v <= 0:
        return 0.0
    return max(1.0, min(5.0, v))


@dataclass
class EvaluationResult:
    """一次评估的结构化结果。"""
    scores: dict[str, dict[str, Any]]
    total_score: float
    hallucination: dict[str, Any]
    summary: str
    model: str
    band: str
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": self.scores,
            "total_score": self.total_score,
            "band": self.band,
            "hallucination": self.hallucination,
            "summary": self.summary,
            "model": self.model,
            "error": self.error,
        }


def _format_sources(sources: list[dict[str, str]]) -> str:
    """把来源列表格式化成 judge 可读的编号列表。"""
    if not sources:
        return "（无来源）"
    lines: list[str] = []
    for i, s in enumerate(sources, 1):
        title = s.get("title") or s.get("url", "")
        url = s.get("url", "")
        lines.append(f"{i}. [{title}]({url})")
    return "\n".join(lines)


def _mean(scores: dict[str, dict[str, Any]]) -> float:
    if not scores:
        return 0.0
    values = [v["score"] for v in scores.values()]
    return round(sum(values) / len(values), 2)


def _parse_dimension(raw: Any) -> dict[str, Any]:
    """把单个维度的原始值解析为 {"score": float, "reason": str}。

    兼容两种 LLM 输出形态：
      * 裸数字：4                     → score=4, reason=""
      * 结构化：{"score": 4, "reason": "...", "missing_angles": [...]}
    """
    reason = ""
    extra: dict[str, Any] = {}

    if isinstance(raw, dict):
        try:
            score = float(raw.get("score", raw.get("value", 0.0)))
        except (TypeError, ValueError):
            score = 0.0
        reason = str(raw.get("reason", "")).strip()
        # 保留分数/原因之外的附加字段（如缺失维度、query 质量细分等），便于回归分析
        extra = {k: v for k, v in raw.items() if k not in ("score", "value", "reason")}
    else:
        try:
            score = float(raw)
        except (TypeError, ValueError):
            score = 0.0

    return {"score": clamp_score(score), "reason": reason, **extra}


async def _call_judge(prompt: str, model: str | None) -> dict[str, Any]:
    """调用 Judge LLM 并解析 JSON，带 3 次「解析失败重试」。

    三次都失败返回空 dict（由调用方降级为 error）。
    """
    last_err: Exception | None = None
    for attempt in range(1, MAX_PARSE_RETRIES + 1):
        try:
            resp = await invoke_eval_llm(prompt, model, streaming=False)
            return parse_json(resp.content)  # 失败会抛 ValueError
        except Exception as exc:  # noqa: BLE001 - 重试逻辑需捕获解析/调用两类异常
            last_err = exc
            logger.warning(
                "Judge 第 %d/%d 次解析失败: %s", attempt, MAX_PARSE_RETRIES, exc
            )
    logger.error("Judge 连续 %d 次解析失败，降级为空结果: %s", MAX_PARSE_RETRIES, last_err)
    return {}


async def evaluate_report(
    report: str,
    sources: list[dict[str, str]] | None = None,
    research_topic: str | None = None,
    model: str | None = None,
) -> EvaluationResult:
    """对最终报告做 5 维度打分 + 幻觉检查（PRD F8）。

    Args:
        report: 最终报告 Markdown 全文。
        sources: 报告实际引用的来源列表 [{title, url}]，可为空。
        research_topic: 研究主题，作为 judge 判断覆盖度/逻辑结构的上下文。
        model: 评估所用的 LLM 模型名（空 → 走 review 角色的默认/覆盖模型）。
    """
    loader = PromptLoader(_PROMPTS_DIR)
    sources = sources or []
    prompt = loader.get(
        "judge",
        research_topic=research_topic or "（未提供）",
        report=report,
        source_count=len(sources),
        sources=_format_sources(sources),
    )

    data = await _call_judge(prompt, model)
    model_label = get_eval_model_label(model)

    if not data:
        empty_scores = {d: {"score": 0.0, "reason": ""} for d in DIMENSIONS}
        return EvaluationResult(
            scores=empty_scores,
            total_score=0.0,
            hallucination={},
            summary="",
            model=model_label,
            band="不可用",
            error="Judge 3 次解析失败，评分不可用",
        )

    # 逐维度容错解析，缺失维度记 0（不因 LLM 少给一项而整体失败）
    scores_raw = data.get("scores", {}) or {}
    scores: dict[str, dict[str, Any]] = {}
    for dim in DIMENSIONS:
        scores[dim] = _parse_dimension(scores_raw.get(dim, 0))

    total = _mean(scores)
    return EvaluationResult(
        scores=scores,
        total_score=total,
        hallucination=data.get("hallucination_check", data.get("hallucination", {})) or {},
        summary=str(data.get("overall_assessment", data.get("summary", ""))).strip(),
        model=model_label,
        band=interpret_band(total),
        raw=data,
    )


def score_value(result: EvaluationResult, dim: str) -> float:
    """取单维度分数（供报告格式化/回归对比使用）。"""
    return result.scores.get(dim, {}).get("score", 0.0)