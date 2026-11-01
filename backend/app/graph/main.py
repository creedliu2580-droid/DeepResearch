"""
LangGraph 主图：Plan Agent（编排者）

核心流程：
plan_node（F1 生成研究计划 → interrupt 暂停）
  ↓ resume 带回 confirmed/feedback
router: plan_confirmed? → research (子图) / plan (replan)
  ↓ research 完成
write (子图)
  ↓ write 完成
qa_node（F7 报告问答）
  ↓
END  —— 下一次追问靠外部再次 resume 进入 qa_node
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph
from langgraph.types import interrupt

from ..settings import get_settings
from ..llm import invoke_llm
from ..prompts import get_prompt_loader
from ..state import GraphState
from ..subgraphs import build_research_subgraph, build_writing_subgraph
from ..citations import dedupe_search_results, make_short_url, restore_urls, resolve_citation_markers, sources_lines
from ..tools import build_search_tool
from ..utils import parse_json

logger = logging.getLogger(__name__)


# ---------- F1 需求澄清 + interrupt ----------

async def plan_node(state: GraphState) -> dict:
    """F1 生成 Markdown 研究计划。

    注意：LangGraph 在 resume 时会从节点开头重放，所以这里必须先查已有计划，
    避免用户确认时又生成一份新计划。
    """
    if state.get("research_plan") and not state.get("force_replan"):
        logger.info("已有研究计划，跳过重复生成")
        return {"current_node": "plan"}

    settings = get_settings()
    effort = state.get("effort", "medium")
    effort_params = settings.effort.get(effort)

    loader = get_prompt_loader()
    prompt = loader.get(
        "plan",
        user_input=state["user_input"],
        effort=effort,
        initial_queries=effort_params["initial_queries"],
        max_rounds=effort_params["max_rounds"],
        plan_feedback=state.get("plan_feedback", "（首轮，无历史修改意见）"),
        effort_note=settings.effort_note(effort, "plan"),
    )

    resp = await invoke_llm("plan", state.get("model_override"), prompt)
    plan_text = resp.content.strip()
    logger.info("研究计划生成完成")

    return {
        "research_plan": plan_text,
        "force_replan": False,
        "current_node": "plan",
        "status": "plan_generated",
    }


async def plan_confirm_node(state: GraphState) -> dict:
    """F1/F2：interrupt 暂停等用户确认或提出修改意见。"""
    human_response = interrupt({
        "reason": "await_plan_confirm",
        "research_plan": state.get("research_plan", ""),
        "question": "请确认研究计划，或告诉我需要修改什么。",
    })

    confirmed = bool(human_response.get("confirmed", False))
    feedback = human_response.get("feedback", "") or ""
    logger.info(f"用户回复: confirmed={confirmed}, feedback={feedback[:50]}")

    return {
        "plan_confirmed": confirmed,
        "plan_feedback": feedback,
        "force_replan": not confirmed,
        "current_node": "plan_confirm",
        "status": "plan_resumed_confirmed" if confirmed else "plan_resumed_replan",
    }


# ---------- F7 报告问答 ----------

async def _judge_qa(
    loader,
    final_report: str,
    question: str,
    model_override: str | None,
) -> tuple[bool, str]:
    """判断报告能否直接回答该追问；不能则给出一个适合搜索引擎的查询。"""
    prompt = loader.get("qa_judge", final_report=final_report, question=question)
    try:
        resp = await invoke_llm("write", model_override, prompt, streaming=False)
        data = parse_json(resp.content)
        return bool(data.get("needs_search", False)), str(data.get("search_query", "")).strip()
    except Exception as exc:
        logger.warning(f"追问相关性判断失败，兜底按报告内回答: {exc}")
        return False, ""


async def _search_and_answer(
    loader,
    question: str,
    search_query: str,
    model_override: str | None,
) -> str:
    """联网搜索后，基于搜索结果回答，并把引用还原成可点击链接。"""
    search_tool = build_search_tool()
    try:
        results = await search_tool.asearch(search_query)
    except Exception as exc:
        logger.warning(f"追问联网搜索失败: {exc}")
        return f"（联网搜索失败，无法获取该问题的最新信息：{exc}）"

    sources = dedupe_search_results([r.to_dict() for r in (results or [])])
    if not sources:
        return "搜索结果中未找到可靠信息，无法回答该问题。"

    # 给每条结果分配短链，LLM 抄短链引用（[来源名](短链URL)），真实链接由 restore_urls 还原
    short_url_map: list[dict[str, str]] = []
    for i, s in enumerate(sources):
        short = make_short_url(0, 0, i)
        s["short_url"] = short
        short_url_map.append({"short": short, "real": s.get("url", "")})
    sources_text = sources_lines(sources, include_query=False)

    prompt = loader.get("qa_search", question=question, sources=sources_text)
    resp = await invoke_llm("write", model_override, prompt)
    return resolve_citation_markers(
        restore_urls(resp.content.strip(), short_url_map),
        sources,
    )


async def qa_node(state: GraphState) -> dict:
    """F7：基于最终报告的追问。报告答不上时自动联网搜索。无待答问题时 interrupt。"""
    loader = get_prompt_loader()

    question = state.get("pending_question", "")
    if not question:
        # 暂停，等下一次 /api/chat/qa 用 Command(resume=question) 唤醒
        question = interrupt({
            "reason": "await_question",
            "final_report": state.get("final_report", ""),
        })

    qa_history = list(state.get("qa_history", []))
    history_text = (
        "\n".join(f"{m['role']}: {m['content']}" for m in qa_history)
        if qa_history else "（首轮问答）"
    )
    final_report = state.get("final_report", "")
    model_override = state.get("model_override")

    # 先判断报告能否回答；不能答则联网搜索
    needs_search, search_query = await _judge_qa(loader, final_report, question, model_override)
    if needs_search:
        answer_text = await _search_and_answer(
            loader, question, search_query or question, model_override
        )
    else:
        prompt = loader.get(
            "qa",
            final_report=final_report,
            qa_history_text=history_text,
            question=question,
        )
        resp = await invoke_llm("write", model_override, prompt)
        answer_text = resp.content.strip()

    new_history = qa_history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer_text},
    ]

    return {
        "qa_history": new_history,
        "pending_question": "",
        "answer": answer_text,
        "current_node": "qa",
        "status": "qa_done",
    }


# ---------- 条件路由 ----------

def route_after_plan(state: GraphState) -> str:
    """plan_confirm 之后的路由 —— 确认则研究，否则重新生成计划。"""
    if state.get("plan_confirmed", False):
        return "research"
    return "plan"


# ---------- 构建主图 ----------

def build_main_graph() -> StateGraph:
    g = StateGraph(GraphState)

    # 顶层节点
    g.add_node("plan", plan_node)
    g.add_node("plan_confirm", plan_confirm_node)
    g.add_node("qa", qa_node)

    # 子图节点
    research_subgraph = build_research_subgraph().compile()
    writing_subgraph = build_writing_subgraph().compile()
    g.add_node("research", research_subgraph)
    g.add_node("write", writing_subgraph)

    # 主流程
    g.set_entry_point("plan")
    g.add_edge("plan", "plan_confirm")
    g.add_conditional_edges("plan_confirm", route_after_plan, {
        "plan": "plan",
        "research": "research",
    })
    g.add_edge("research", "write")
    g.add_edge("write", "qa")
    g.add_edge("qa", "qa")  # 答完一题后再次进入 qa，interrupt 等待下一次追问

    return g


# ---------- 编译 + 单例 ----------

_graph: Any = None


def compile_graph(checkpointer: Any) -> Any:
    """用给定检查点编译主图。检查点的创建/关闭由调用方（FastAPI lifespan 或测试）负责。"""
    return build_main_graph().compile(checkpointer=checkpointer)


def set_graph(graph: Any) -> None:
    """注入已编译的图（FastAPI lifespan 在启动时调用）。"""
    global _graph
    _graph = graph


def get_graph() -> Any:
    """拿已编译的图单例。未初始化（lifespan 未运行）时抛错，避免静默用错。"""
    if _graph is None:
        raise RuntimeError("graph 未初始化：请确认 FastAPI lifespan 已运行（不要绕过 app 启动流程）。")
    return _graph
