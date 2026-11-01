"""
LangGraph 子图：Research Agent（F3 深度研究）

改造（2026-10-29）：
  - 搜索异步并行（asearch_batch）
  - 每轮搜索后立刻压缩成 bullet 摘要（_summarize_round），
    research_summary 不再累积原始 content，而是累积每轮摘要。
    好处：reflect/outline/draft 的 LLM 输入 token 大幅降低。

职责：
1. 将研究主题拆分成多个独立搜索查询
2. 并行调用 Tavily 搜索
3. 每轮搜索后压缩成本轮摘要，累积到 research_summary
4. 反思信息是否充足，不足则生成补充查询继续搜索（循环 ≤ max_rounds）
5. 反思评估失败时视为"信息不足但不补搜"（安全兜底避免死循环）
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from ..utils import parse_json, parse_json_list
from ..settings import get_settings
from ..llm import invoke_llm
from ..prompts import get_prompt_loader
from ..state import GraphState
from ..tools import build_search_tool
from ..citations import make_short_url, _source_name_from_url

logger = logging.getLogger(__name__)


# ---------- 节点实现 ----------

async def split_queries_node(state: GraphState) -> dict:
    """F3.1 拆分查询。"""
    settings = get_settings()
    effort = state.get("effort", "medium")
    count = settings.effort.get(effort)["initial_queries"]

    loader = get_prompt_loader()
    prompt = loader.get(
        "split_queries",
        research_plan=state["research_plan"],
        count=count,
        effort_note=settings.effort_note(effort, "split_queries"),
    )

    resp = await invoke_llm("search", state.get("model_override"), prompt, streaming=False)

    queries = parse_json_list(resp.content, cap=count)
    logger.info(f"拆分查询: {queries}")

    return {
        "search_queries": list(state.get("search_queries", [])) + queries,
        "pending_queries": queries,
        "search_round": 0,
        "round_summaries": [],
        "research_summary": "",
        "current_node": "split_queries",
        "status": "split_queries_done",
    }


async def search_node(state: GraphState) -> dict:
    """F3.2 并行搜索（本轮原始结果交给 reflect 做「摘要 + 反思」）。"""
    search_tool = build_search_tool()
    queries = state.get("pending_queries", [])
    current_round = state.get("search_round", 0)
    round_no = current_round + 1

    # === 并行搜索 + 分配短链（round-query-result） ===
    short_url_map = list(state.get("short_url_map", []))
    results_raw: list[dict] = []

    if queries:
        # asearch_batch 返回与 queries 一一对应的结果列表，据此生成短链
        grouped = await search_tool.asearch_batch(queries)
        for query_idx, hits in enumerate(grouped):
            for result_idx, hit in enumerate(hits):
                d = hit.to_dict()
                short = make_short_url(round_no, query_idx, result_idx)
                d["short_url"] = short
                short_url_map.append({"short": short, "real": d.get("url", "")})
                results_raw.append(d)

    logger.info(f"第 {round_no} 轮搜索完成，新增 {len(results_raw)} 条结果")

    # === 累积原始结果（保留给未来 Top-K / QA 用） ===
    all_results = list(state.get("search_results", [])) + results_raw

    return {
        "search_results": all_results,
        "short_url_map": short_url_map,
        "pending_results": results_raw,  # 本轮原始结果，交给 reflect 做「摘要 + 反思」
        "pending_queries": [],
        "search_round": round_no,
        "current_node": "search",
        "status": "search_done",
    }


async def reflect_node(state: GraphState) -> dict:
    """F3.3 摘要 + 反思（合并为一次 LLM 调用）：压缩本轮结果 + 判断是否补搜。"""
    settings = get_settings()
    loader = get_prompt_loader()

    effort = state.get("effort", "medium")
    max_rounds = settings.effort.get(effort)["max_rounds"]
    current_round = state.get("search_round", 0)

    pending = state.get("pending_results", [])
    if not pending:
        # 无结果（如搜索失败/空）——直接结束
        return {"current_node": "reflect", "status": "research_done"}

    # 格式化本轮原始结果（截断 content，给来源简称）
    results_lines = []
    for i, r in enumerate(pending, 1):
        content = (r.get("content", "") or "")[:400].replace("\n", " ")
        name = _source_name_from_url(r.get("url", "") or "")
        results_lines.append(f"[{i}] 来源「{name}」 {r.get('title', '')}: {content}")

    prompt = loader.get(
        "summarize_and_reflect",
        results="\n".join(results_lines),
        research_summary=state.get("research_summary", ""),
        reflections="\n".join(state.get("reflections", [])[-5:]),
        max_additional=5,
        effort_note=settings.effort_note(effort, "reflect"),
    )

    try:
        resp = await invoke_llm("search", state.get("model_override"), prompt, streaming=False)
        decision = parse_json(resp.content)
    except Exception as exc:
        logger.warning(f"摘要+反思失败，兜底结束: {exc}")
        return {"current_node": "reflect", "status": "research_done"}

    summary = (decision.get("summary", "") or "").strip()
    info_sufficient = decision.get("information_sufficient", True)
    additional = decision.get("additional_queries", [])
    reason = decision.get("reason", "")

    # 累积摘要（本轮 summary 追加进 research_summary）
    prev_summaries = list(state.get("round_summaries", []))
    if summary:
        prev_summaries.append(f"[第 {current_round} 轮摘要]\n{summary}")
    research_summary = "\n\n".join(prev_summaries)

    logger.info(f"反思: sufficient={info_sufficient}, reason={reason}, additional={additional}")

    base = {
        "round_summaries": prev_summaries,
        "research_summary": research_summary,
        "pending_results": [],
    }

    # 达最大轮次 → 强制结束（本轮摘要已入 research_summary）
    if current_round >= max_rounds:
        logger.info(f"已达最大搜索轮次 {max_rounds}，结束研究")
        return {
            **base,
            "reflections": list(state.get("reflections", [])) + [f"[第{current_round}轮] 达最大轮次: {reason}"],
            "current_node": "reflect",
            "status": "research_done",
        }

    if info_sufficient or not additional:
        return {
            **base,
            "reflections": list(state.get("reflections", [])) + [f"[第{current_round}轮] 信息充足: {reason}"],
            "current_node": "reflect",
            "status": "research_done",
        }

    return {
        **base,
        "reflections": list(state.get("reflections", [])) + [f"[第{current_round}轮] 需要补搜: {reason}"],
        "search_queries": list(state.get("search_queries", [])) + additional,
        "pending_queries": additional,
        "current_node": "reflect",
        "status": "need_more_search",
    }


# ---------- 条件路由 ----------

def route_after_split(state: GraphState) -> str:
    return "search"


def route_after_reflect(state: GraphState) -> str:
    status = state.get("status", "")
    if status == "research_done":
        return END
    return "search"


# ---------- 构建子图 ----------

def build_research_subgraph() -> StateGraph:
    g = StateGraph(GraphState)

    g.add_node("split_queries", split_queries_node)
    g.add_node("search", search_node)
    g.add_node("reflect", reflect_node)

    g.set_entry_point("split_queries")
    g.add_edge("split_queries", "search")
    g.add_edge("search", "reflect")
    g.add_conditional_edges("reflect", route_after_reflect, {
        "search": "search",
        END: END,
    })

    return g
