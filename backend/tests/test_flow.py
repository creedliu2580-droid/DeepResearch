"""端到端流程自测：用假模型 + 假搜索跑通
plan → interrupt 确认 → research 子图 → write 子图 → qa 问答（无需真实 LLM / Tavily key）。

运行：
    python tests/test_flow.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command


class _Resp:
    def __init__(self, content):
        self.content = content


class FakeModel:
    """按 prompt 里的特征词区分调用节点，返回对应的假输出。"""

    async def ainvoke(self, prompt, **kwargs):
        text = prompt if isinstance(prompt, str) else str(prompt)

        if "information_sufficient" in text:  # summarize_and_reflect（摘要+反思合并）
            return _Resp(
                '{"summary": "- 关键要点一（示例来源）", '
                '"information_sufficient": true, "reason": "资料已足够", "additional_queries": []}'
            )
        if "研究策略师" in text:  # split_queries
            return _Resp('["查询A", "查询B", "查询C"]')
        if "学术审稿人" in text:  # review
            return _Resp(
                '{"scores": {"事实准确性":8,"证据质量":8,"目标对齐":8,"结构逻辑":8,"内容深度":8,"表达清晰度":8},'
                ' "total_score": 8.0, "issues": [], "ready_for_polish": true, "summary": "ok"}'
            )
        if "技术写作者" in text:  # outline
            return _Resp("## 概述\n### 背景\n## 市场分析\n### 规模")
        if "研究报告撰写人" in text:  # draft
            return _Resp("# 报告\n\n正文内容。[来源：示例标题(https://example.com/a)]")
        if "资深编辑" in text:  # polish
            return _Resp("# 报告（最终）\n\n正文。")
        if "资深研究分析师" in text:  # plan
            return _Resp("# 研究计划\n## 研究主题与核心问题\n- 核心问题1\n## 下一步\n开始研究")
        if "研究助理" in text:  # qa
            return _Resp("根据报告，结论是……")
        return _Resp("ok")


class FakeModelFactory:
    def __init__(self):
        self.model = FakeModel()

    def get(self, role, model=None, streaming=True):
        return self.model


class _Result:
    def __init__(self, query, i):
        self.query = query
        self.title = f"{query} 结果{i}"
        self.url = f"https://example.com/{i}"
        self.content = "这是一段内容摘要"

    def to_dict(self):
        return {"query": self.query, "title": self.title, "url": self.url, "content": self.content}


class FakeSearch:
    async def asearch_batch(self, queries, max_results=5):
        return [[_Result(q, i) for i in range(2)] for q in queries]


async def main():
    import aiosqlite

    import app.llm as llm_mod
    import app.subgraphs.research as research_mod
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    fake_factory = FakeModelFactory()
    llm_mod.get_model_factory = lambda: fake_factory
    research_mod.build_search_tool = lambda: FakeSearch()

    from app.graph import build_main_graph

    conn = await aiosqlite.connect(":memory:")
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()
    graph = build_main_graph().compile(checkpointer=checkpointer)

    cfg = {"configurable": {"thread_id": "t-flow"}}

    # 1. 启动：plan → plan_confirm interrupt
    async for _ in graph.astream(
        {"user_input": "分析AI编程工具", "effort": "medium", "model_override": "", "thread_id": "t-flow"},
        cfg,
    ):
        pass

    st = await graph.aget_state(cfg)
    intr = st.tasks[0].interrupts[0].value
    assert intr.get("reason") == "await_plan_confirm", intr
    print("PASS 1: plan 生成后 interrupt 等待确认")
    print("  research_plan 片段:", st.values.get("research_plan", "")[:30])

    # 2. 确认计划 → research → write → qa interrupt
    async for _ in graph.astream(Command(resume={"confirmed": True, "feedback": ""}), cfg):
        pass

    st = await graph.aget_state(cfg)
    vals = st.values
    assert vals.get("final_report"), "应已生成最终报告"
    intr2 = st.tasks[0].interrupts[0].value
    assert intr2.get("reason") == "await_question", intr2
    print("PASS 2: research + write 完成，final_report 已生成，qa interrupt 等待提问")
    print("  final_report 片段:", vals.get("final_report", "")[:40])
    print("  search_results 数:", len(vals.get("search_results", [])))
    print("  round_summaries 数:", len(vals.get("round_summaries", [])))

    # 3. 追问 → qa 回答 → 再次 interrupt
    async for _ in graph.astream(Command(resume="这个市场有什么趋势？"), cfg):
        pass

    st = await graph.aget_state(cfg)
    assert st.values.get("answer"), "应有 QA 回答"
    print("PASS 3: qa 回答完成")
    print("  answer 片段:", st.values.get("answer", "")[:40])

    await conn.close()
    print("\n全部流程通过")


def test_flow():
    """pytest 入口：跑完整流程自测（假模型 + 假搜索，无需真实 key）。"""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
