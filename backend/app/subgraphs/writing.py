"""
LangGraph 子图：Writing Agent（F4 报告撰写）

职责：
1. 基于研究计划 + 搜索摘要生成大纲
2. 按大纲撰写草稿
3. 审稿评分（0-10，5维度）+ 列出 critical/major/minor 问题
4. 审稿 ready 则润色并结束；否则回到草稿节点修订，最多修订 2 次兜底
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from ..citations import dedupe_search_results, sources_lines, restore_urls, resolve_citation_markers, strip_source_sections, check_number_grounding
from ..utils import parse_json
from ..settings import get_settings
from ..llm import invoke_llm
from ..prompts import get_prompt_loader
from ..state import GraphState

logger = logging.getLogger(__name__)


# ---------- 节点 ----------

async def outline_node(state: GraphState) -> dict:
    """F4.1 大纲生成。"""
    loader = get_prompt_loader()

    prompt = loader.get(
        "outline",
        research_plan=state["research_plan"],
        research_summary=state.get("research_summary", ""),
    )
    resp = await invoke_llm("write", state.get("model_override"), prompt, streaming=False)

    return {
        "report_outline": resp.content.strip(),
        "current_node": "outline",
        "status": "outline_done",
    }


async def draft_node(state: GraphState) -> dict:
    """F4.2 草稿撰写 / 修订。"""
    loader = get_prompt_loader()
    revision_round = state.get("revision_round", 0)

    # 把审稿问题拼成可读文本
    issues = state.get("review_issues", [])
    if issues:
        parts = []
        for i in issues:
            base = f"- [{i.get('severity','?')}] {i.get('location','')}: {i.get('description','')}"
            if i.get("suggestion"):
                base += f"（建议：{i.get('suggestion')}）"
            parts.append(base)
        issues_text = "\n".join(parts)
    else:
        issues_text = "（首轮撰写，无审稿意见）"

    # 从原始搜索结果中提取带 URL 的来源列表 —— LLM 写 [来源名](短链) 时要用
    sources_text = sources_lines(dedupe_search_results(state.get("search_results", [])))

    prompt = loader.get(
        "draft",
        report_outline=state["report_outline"],
        research_summary=state.get("research_summary", ""),
        sources=sources_text,
        revision_round=revision_round,
        review_issues_text=issues_text,
        previous_draft=state.get("report_draft", ""),
    )

    resp = await invoke_llm("write", state.get("model_override"), prompt, streaming=False)

    new_round = revision_round + 1
    return {
        "report_draft": resp.content.strip(),
        "revision_round": new_round,
        # 清空本轮旧问题，让 review_node 重新评估
        "review_issues": [],
        "current_node": "draft",
        "status": "draft_done",
    }


async def review_node(state: GraphState) -> dict:
    """F4.3 审稿。"""
    loader = get_prompt_loader()
    prompt = loader.get(
        "review",
        report_text=state["report_draft"],
        research_plan=state["research_plan"],
        research_summary=state.get("research_summary", ""),
    )

    try:
        resp = await invoke_llm("review", state.get("model_override"), prompt, streaming=False)
        decision = parse_json(resp.content)
    except Exception as exc:
        logger.warning(f"审稿解析失败，兜底放行: {exc}")
        return {
            "review_score": 7.0,
            "review_issues": [],
            "current_node": "review",
            "status": "review_pass",
        }

    scores = decision.get("scores", {})
    total = decision.get("total_score", sum(scores.values()) / len(scores) if scores else 5.0)
    issues = decision.get("issues", [])

    total = float(total)
    revision_round = state.get("revision_round", 0)  # 1=首稿，之后每写一次 +1
    revisions_done = max(0, revision_round - 1)       # 0=首稿，1=第1次修订，2=第2次修订

    settings = get_settings()

    # 退出规则：
    #   1. 评分 ≥ 8 → 直接过
    #   2. 评分 ≥ pass_score(6.0) 且 已修订 ≥ 1 次 → 过
    #   3. 已修订 ≥ max_revisions(2) 次 → 强制过
    ready = total >= 8.0 or (total >= settings.writing.pass_score and revisions_done >= 1)
    if revisions_done >= settings.writing.max_revisions:
        ready = True
        logger.warning("已达最大修订次数 %d，强制放行", settings.writing.max_revisions)

    logger.info(
        f"审稿: score={total:.1f}, issues={len(issues)}, revisions_done={revisions_done}, "
        f"ready={ready}"
    )

    return {
        "review_score": total,
        "review_issues": issues,
        "current_node": "review",
        "status": "review_pass" if ready else "need_revision",
    }


async def polish_node(state: GraphState) -> dict:
    """F4.4 最终润色。"""
    loader = get_prompt_loader()

    # 同样提取 sources，避免润色时丢掉来源
    sources = dedupe_search_results(state.get("search_results", []))
    sources_text = sources_lines(sources, include_query=False)

    prompt = loader.get("polish", draft_text=state["report_draft"], sources=sources_text)

    resp = await invoke_llm("write", state.get("model_override"), prompt)

    # 短链 → 真实链接（restore_urls）；兼容遗留 [编号]；并剥掉 LLM 误输出的「来源列表」等章节
    final_report = strip_source_sections(
        resolve_citation_markers(
            restore_urls(resp.content.strip(), state.get("short_url_map", [])),
            sources,
        )
    )

    # 生成后兜底核对：报告里的数字若在「摘要 + 原始来源内容」里都找不到出处，才记为疑似无出处（不写进正文）
    reference = state.get("research_summary", "") + "\n" + "\n".join(
        s.get("content", "") or "" for s in sources
    )
    ungrounded = check_number_grounding(final_report, reference)
    if ungrounded:
        logger.warning("疑似无出处数字: %s", ungrounded)

    return {
        "final_report": final_report,
        "ungrounded_numbers": ungrounded,
        "current_node": "polish",
        "status": "report_done",
    }


# ---------- 条件路由 ----------

def route_after_review(state: GraphState) -> str:
    """ready → polish；否则回到 draft 修订。"""
    return "polish" if state.get("status") == "review_pass" else "draft"


# ---------- 构建子图 ----------

def build_writing_subgraph() -> StateGraph:
    g = StateGraph(GraphState)

    g.add_node("outline", outline_node)
    g.add_node("draft", draft_node)
    g.add_node("review", review_node)
    g.add_node("polish", polish_node)

    g.set_entry_point("outline")
    g.add_edge("outline", "draft")
    g.add_edge("draft", "review")
    g.add_conditional_edges("review", route_after_review, {
        "polish": "polish",
        "draft": "draft",
    })
    g.add_edge("polish", END)

    return g
