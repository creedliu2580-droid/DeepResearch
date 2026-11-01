"""
GraphState — LangGraph 主图 + 所有子图共享的状态定义。
所有字段用 Annotated 标注 reducer 函数（列表追加、字符串覆盖等），
主图和子图通过 StateGraph 注入后自动对齐 schema。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from typing_extensions import NotRequired, TypedDict


# ---------- Reducer 函数（LangGraph State Annotation 规范） ----------
def _overwrite(left: Any, right: Any) -> Any:
    """覆盖式合并（标量/字符串/列表）。

    子图节点会同时写回子图与父图，使用 append reducer 会导致数据重复，
    因此列表也在节点内手动累积后整体覆盖。
    """
    return right


# ---------- 主图 + 子图共享状态 ----------
class GraphState(TypedDict, total=False):
    # ===== 输入 =====
    user_input: str
    effort: Literal["low", "medium", "high"]
    model_override: str                                    # 前端所选模型，空则按角色配置

    # ===== F1 需求澄清 =====
    research_plan: Annotated[str, _overwrite]              # Markdown 研究计划
    plan_feedback: Annotated[str, _overwrite]              # 用户反馈（确认 / 修改意见）
    plan_confirmed: Annotated[bool, _overwrite]
    force_replan: Annotated[bool, _overwrite]              # 用户要求修改 → 重新生成计划

    # ===== F3 研究子图 =====
    search_queries: Annotated[list[str], _overwrite]       # 历史查询（节点内手动累积）
    pending_queries: Annotated[list[str], _overwrite]      # 本轮待搜索的查询
    search_results: Annotated[list[dict], _overwrite]      # 每条含 {query, title, url, content, short_url}
    short_url_map: Annotated[list[dict], _overwrite]       # 累积 [{short, real}]，短链→真实 URL
    pending_results: Annotated[list[dict], _overwrite]     # 本轮原始搜索结果（交给 reflect 做摘要+反思）
    round_summaries: Annotated[list[str], _overwrite]      # 每轮搜索后的压缩摘要（节点内手动累积）
    research_summary: Annotated[str, _overwrite]           # 拼接所有 round_summaries，喂给 LLM
    reflections: Annotated[list[str], _overwrite]
    search_round: Annotated[int, _overwrite]

    # ===== F4 写作子图 =====
    report_outline: Annotated[str, _overwrite]
    report_draft: Annotated[str, _overwrite]
    review_score: Annotated[float, _overwrite]
    review_issues: Annotated[list[dict], _overwrite]       # [{severity, description, location}]
    revision_round: Annotated[int, _overwrite]
    final_report: Annotated[str, _overwrite]
    ungrounded_numbers: Annotated[list[str], _overwrite]     # 疑似无出处的数字（仅记录，不写入正文）

    # ===== F7 报告问答 =====
    qa_history: Annotated[list[dict], _overwrite]          # [{role, content}]
    pending_question: Annotated[str, _overwrite]           # 待回答的追问
    answer: Annotated[str, _overwrite]

    # ===== 会话控制 =====
    status: Annotated[str, _overwrite]                     # 给前端时间线用
    thread_id: Annotated[str, _overwrite]
    current_node: Annotated[str, _overwrite]               # 当前执行的节点名
