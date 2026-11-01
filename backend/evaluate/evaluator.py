"""
E2E 评估编排器 —— 从头跑真实 agent 流水线，对最终报告评分。

对齐参考项目（DeepResearch-06/eval/evaluator.py）的「两阶段调用」模式：
本项目主图在 plan_confirm 节点通过 interrupt 等用户确认，因此 E2E 评估分两步：
  (1) ainvoke 初始输入 → 生成研究计划 → 停在 plan_confirm 的 interrupt；
  (2) Command(resume=确认) → 继续跑 research → write → 产出 final_report。

报告产出后，交给 judge 按 PRD F8 的 5 维度 + 幻觉检查打分。

注意：本步骤会真实消耗 Tavily 搜索与 LLM 配额（每个主题跑一整条研究流水线）。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph import build_main_graph
from evaluate.judge import EvaluationResult, evaluate_report

logger = logging.getLogger("evaluate.evaluator")


# ---------- 数据类型 ----------

@dataclass
class TopicCfg:
    """单个主题的评估配置（对应 test_set.json 顶级字段）。"""
    topic: str
    effort: str = "medium"
    expected_key_facts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TopicCfg":
        return cls(
            topic=d["topic"],
            effort=d.get("effort", "medium"),
            expected_key_facts=list(d.get("expected_key_facts", [])),
        )


@dataclass
class E2EResult:
    topic: str
    effort: str = "medium"
    report: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    score: EvaluationResult | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "effort": self.effort,
            "report_len": len(self.report),
            "sources": self.sources,
            "score": self.score.to_dict() if self.score else None,
            "error": self.error,
        }


# ---------- 辅助 ----------

def _extract_sources(search_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """从 state.search_results 提取 judge 需要的 {title, url} 列表，按 URL 去重。"""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in search_results or []:
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({
            "title": (item.get("title") or "").strip() or url,
            "url": url,
        })
    return out


async def _run_agent(topic_cfg: TopicCfg, agent_model: str | None) -> tuple[str, list[dict[str, str]], str]:
    """跑完整 agent 流水线，返回 (final_report, sources, effort)。

    每次评估用独立的 InMemorySaver + 独立 thread_id，避免污染生产 checkpoints。
    """
    effort = topic_cfg.effort
    graph = build_main_graph().compile(checkpointer=InMemorySaver())
    thread_id = f"eval-{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}

    input_state = {
        "user_input": topic_cfg.topic,
        "effort": effort,
        "model_override": (agent_model or "").strip(),
        "thread_id": thread_id,
    }

    # 阶段 1：生成计划，停在 plan_confirm 的 interrupt
    await graph.ainvoke(input_state, config)

    # 阶段 2：自动确认计划，继续 research → write → qa（qa 会再次 interrupt）
    await graph.ainvoke(
        Command(resume={"confirmed": True, "feedback": ""}),
        config,
    )

    state = await graph.aget_state(config)
    values = state.values if state else {}
    final_report = (values.get("final_report") or "").strip()
    sources = _extract_sources(values.get("search_results", []))
    return final_report, sources, effort


# ---------- 编排 ----------

async def run_e2e_single(
    topic_cfg: TopicCfg,
    judge_model: str | None = None,
    agent_model: str | None = None,
) -> E2EResult:
    """对单个主题跑完整流水线并评分。"""
    result = E2EResult(topic=topic_cfg.topic)
    try:
        report, sources, effort = await _run_agent(topic_cfg, agent_model)
        result.effort = effort
        result.report = report
        result.sources = sources

        if not report:
            result.error = "流水线未产出最终报告"
            return result

        result.score = await evaluate_report(
            report=report,
            sources=sources,
            research_topic=topic_cfg.topic,
            model=judge_model,
        )
    except Exception as exc:  # noqa: BLE001 - 评估框架需兜底，不因单主题失败中断整个批次
        logger.exception("端到端评估失败 '%s': %s", topic_cfg.topic[:60], exc)
        result.error = str(exc)
    return result


async def run_e2e(
    topics: list[TopicCfg],
    judge_model: str | None = None,
    agent_model: str | None = None,
) -> list[E2EResult]:
    """顺序跑一批主题（评估属低频任务，顺序执行已足够，避免并发热点）。

    遇到 Tavily 额度用尽（HTTP 432）时提前中止，剩余主题标记为跳过，避免反复撞墙。
    """
    results: list[E2EResult] = []
    for i, cfg in enumerate(topics, 1):
        t0 = time.monotonic()
        logger.info("E2E [%d/%d] 主题=%s", i, len(topics), cfg.topic[:80])
        result = await run_e2e_single(cfg, judge_model, agent_model)
        results.append(result)
        logger.info(
            "E2E [%d/%d] 主题=%s 用时 %.1fs",
            i, len(topics), cfg.topic[:80], time.monotonic() - t0,
        )

        if _is_quota_exhausted(result.error):
            logger.warning("检测到 Tavily 额度用尽，中止剩余 %d 个主题", len(topics) - i)
            for remaining in topics[i:]:
                results.append(E2EResult(topic=remaining.topic, error="跳过：Tavily 额度已用尽"))
            break
    return results


def _is_quota_exhausted(err: str | None) -> bool:
    """判断错误是否是 Tavily 用量/额度超限（HTTP 432）。"""
    return bool(err) and ("432" in err or "额度已用尽" in err)