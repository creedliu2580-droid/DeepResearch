"""
FastAPI 入口 — SSE 网关 + REST 接口。

核心路由：
- GET  /api/models              返回角色→模型配置
- POST /api/chat/start         新建会话 → plan_node → interrupt
- POST /api/chat/resume        Command(resume={confirmed, feedback}) 恢复 interrupt
- POST /api/chat/qa            追加 qa_history +  resume_as_node="qa"
- GET  /api/chat/state/{tid}   调试
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse, JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from .settings import get_settings
from .graph import build_main_graph, get_graph, set_graph
from .models import get_model_factory
from .citations import collect_citations, strip_source_sections, append_sources_section
from .observability import (
    setup_logging,
    record_http,
    request_id_var,
    metrics_text,
    uptime_seconds,
)
from . import sessions
from .export import build_report_markdown, export_docx, export_pdf

setup_logging(
    level=logging.INFO,
    json_format=os.environ.get("LOG_JSON", "").lower() in ("1", "true", "yes"),
)
logger = logging.getLogger("main")

settings = get_settings()

# 限流状态
_rate_lock = threading.Lock()
_rate_buckets: dict[str, tuple[float, int]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时创建 SQLite 检查点、会话元数据表并编译图；关闭时释放连接。"""
    await sessions.init()
    try:
        async with AsyncSqliteSaver.from_conn_string(settings.server.checkpoint_db_path) as checkpointer:
            await checkpointer.setup()
            set_graph(build_main_graph().compile(checkpointer=checkpointer))
            logger.info("已挂载 SQLite 检查点: %s", settings.server.checkpoint_db_path)
            yield
    finally:
        set_graph(None)
        await sessions.close()


app = FastAPI(title="DeepResearch API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request, call_next):
    """为每个请求打 request-id、做简单限流、记录请求指标。"""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request_id_var.set(rid)

    # 固定窗口限流（按客户端 IP）
    now = time.monotonic()
    client = request.client.host if request.client else "unknown"
    with _rate_lock:
        start, count = _rate_buckets.get(client, (now, 0))
        if now - start > 60.0:
            start, count = now, 0
        if count >= settings.concurrency.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
                headers={"X-Request-ID": rid},
            )
        _rate_buckets[client] = (start, count + 1)

    t0 = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        record_http(request.method, request.url.path, 500, time.monotonic() - t0)
        raise
    record_http(request.method, request.url.path, response.status_code, time.monotonic() - t0)
    response.headers["X-Request-ID"] = rid
    return response


# ---------- 请求模型 ----------

class StartRequest(BaseModel):
    user_input: str
    effort: str = "medium"
    model: str = ""


class ResumeRequest(BaseModel):
    thread_id: str
    feedback: str = ""
    confirmed: bool = False


class QARequest(BaseModel):
    thread_id: str
    question: str


# ---------- 辅助 ----------

def _sse_dump(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _cfg(tid: str) -> dict:
    return {"configurable": {"thread_id": tid}}


def _evt(etype: str, **kwargs) -> dict:
    return {"type": etype, "ts": datetime.utcnow().isoformat(), **kwargs}


def _summarize(output) -> str:
    if not isinstance(output, dict):
        return str(output)[:80]
    for key in ("current_node", "status", "final_report", "answer"):
        if key in output:
            v = output[key]
            return (v if isinstance(v, str) else str(v))[:80]
    return ""


async def _emit_events(iterator, thread_id: str, emit_report: bool = True) -> AsyncGenerator[str, None]:
    """把 LangGraph 事件迭代器转成 SSE。"""
    try:
        async for event in iterator:
            kind = event.get("event")
            name = event.get("name", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    # name 是模型名（如 ChatOpenAI），真正的图节点名在 metadata.langgraph_node
                    node_name = event.get("metadata", {}).get("langgraph_node", name)
                    yield _sse_dump(_evt("token", node=node_name, content=chunk.content))

            elif kind == "on_chain_start":
                yield _sse_dump(_evt("node_start", node=name))

            elif kind == "on_chain_end":
                data = event.get("data", {})
                output = data.get("output", {})
                summary = _summarize(output)
                yield _sse_dump(_evt("node_end", node=name, summary=summary))

    except Exception as exc:
        logger.exception("Stream error")
        yield _sse_dump(_evt("error", message=str(exc)))
        yield _sse_dump(_evt("done"))
        return

    # 流结束后：先下发最终报告，再检查是否有待恢复的中断
    try:
        state = await get_graph().aget_state(_cfg(thread_id))
        vals = state.values
        if emit_report and vals.get("final_report"):
            # 只返回终稿里实际被引用的来源（短链已还原、按 URL 去重、杜绝幻觉）
            sources = collect_citations(
                vals.get("final_report", ""),
                vals.get("short_url_map", []),
                vals.get("search_results", []),
            )
            yield _sse_dump(_evt("report_done", report=vals["final_report"], sources=sources))
        if vals.get("answer"):
            # 追问答案：把解析后的终稿答案（编号已还原为真实链接）下发给前端覆盖流式原文
            yield _sse_dump(_evt("answer_done", answer=vals["answer"]))
        pending = bool(state.tasks and state.tasks[0].interrupts)
        if pending:
            payload = state.tasks[0].interrupts[0].value
            if not isinstance(payload, dict):
                payload = {}
            yield _sse_dump(_evt(
                "interrupt",
                reason=payload.get("reason", "await_user"),
                data=payload,
            ))
    except Exception:
        logger.warning("check interrupt state failed", exc_info=True)

    yield _sse_dump(_evt("done"))


# ---------- 路由 ----------

@app.get("/api/models")
async def list_models():
    factory = get_model_factory()
    return {
        "models": factory.list_available(),
        "available": factory.list_candidates(),
    }


@app.post("/api/chat/start")
async def start_chat(req: StartRequest):
    thread_id = uuid.uuid4().hex
    await sessions.upsert(thread_id, req.user_input)
    logger.info(f"[start] thread={thread_id}, input={req.user_input[:50]}")

    graph = get_graph()
    cfg = _cfg(thread_id)
    input_state = {
        "user_input": req.user_input,
        "effort": req.effort,
        "model_override": req.model.strip(),
        "thread_id": thread_id,
    }

    async def event_stream():
        # 首帧下发 session，前端据此拿到真实 thread_id
        yield _sse_dump(_evt("session", thread_id=thread_id))
        async for chunk in _emit_events(
            graph.astream_events(input_state, cfg, version="v2"), thread_id
        ):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/chat/resume")
async def resume_chat(req: ResumeRequest):
    """恢复 interrupt —— 用 Command(resume=...)。"""
    graph = get_graph()
    cfg = _cfg(req.thread_id)

    state = await graph.aget_state(cfg)
    if state is None:
        raise HTTPException(status_code=404, detail=f"thread {req.thread_id} not found")

    await sessions.touch(req.thread_id)
    resume_value = {
        "confirmed": req.confirmed,
        "feedback": req.feedback,
    }
    logger.info(f"[resume] thread={req.thread_id}, {resume_value}")

    iterator = graph.astream_events(Command(resume=resume_value), cfg, version="v2")
    return StreamingResponse(
        _emit_events(iterator, req.thread_id),
        media_type="text/event-stream",
    )


@app.post("/api/chat/qa")
async def chat_qa(req: QARequest):
    """追加 QA 历史并 resume 到 qa 节点。"""
    graph = get_graph()
    cfg = _cfg(req.thread_id)

    state = await graph.aget_state(cfg)
    if state is None:
        raise HTTPException(status_code=404, detail=f"thread {req.thread_id} not found")

    await sessions.touch(req.thread_id)
    logger.info(f"[qa] thread={req.thread_id}, q={req.question[:50]}")

    # 用追问文本唤醒 qa_node 里的 interrupt
    iterator = graph.astream_events(Command(resume=req.question), cfg, version="v2")
    return StreamingResponse(
        _emit_events(iterator, req.thread_id, emit_report=False),
        media_type="text/event-stream",
    )


@app.get("/api/chat/state/{thread_id}")
async def get_state(thread_id: str):
    graph = get_graph()
    cfg = _cfg(thread_id)
    state = await graph.aget_state(cfg)
    if state is None:
        raise HTTPException(status_code=404, detail=f"thread {thread_id} not found")
    return {
        "thread_id": thread_id,
        "next": list(state.next) if state.next else [],
        "values": state.values,
        "has_interrupt": bool(state.tasks and state.tasks[0].interrupts),
    }


@app.get("/api/report/{thread_id}")
async def get_report(thread_id: str):
    """获取最终报告 JSON — 前端在新 tab 打开预览时用。"""
    graph = get_graph()
    cfg = _cfg(thread_id)
    state = await graph.aget_state(cfg)
    if state is None:
        raise HTTPException(status_code=404, detail=f"thread {thread_id} not found")
    vals = state.values
    final_report = vals.get("final_report", "")
    if not final_report:
        raise HTTPException(status_code=404, detail="该会话尚未生成最终报告")

    # 只返回终稿里实际被引用的来源
    sources = collect_citations(
        final_report,
        vals.get("short_url_map", []),
        vals.get("search_results", []),
    )

    return {
        "thread_id": thread_id,
        "report": final_report,
        "sources": sources[:30],
        "generated_at": state.metadata.get("step_ts") if hasattr(state, "metadata") else None,
    }


@app.get("/api/report/download/{thread_id}")
async def download_report(thread_id: str):
    """下载最终报告为 Markdown 文件。"""
    graph = get_graph()
    cfg = _cfg(thread_id)
    state = await graph.aget_state(cfg)
    if state is None:
        raise HTTPException(status_code=404, detail=f"thread {thread_id} not found")
    final_report = state.values.get("final_report", "")
    if not final_report:
        raise HTTPException(status_code=404, detail="该会话尚未生成最终报告")

    # 剥掉历史会话里可能残留的来源章节，再补上统一的信息来源
    sources = collect_citations(
        final_report,
        state.values.get("short_url_map", []),
        state.values.get("search_results", []),
    )
    final_report = append_sources_section(strip_source_sections(final_report), sources)

    # 文件名：取用户输入的前 20 字 + 时间戳
    user_input = state.values.get("user_input", "research").strip()
    safe_name = "".join(c for c in user_input if c.isalnum() or c in " -_")[:20] or "research"
    filename = f"deepresearch_{safe_name}.md"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename.encode("utf-8", "replace").decode("ascii", "replace")}"',
        "X-Filename": filename,  # 前端可以读这个 header
    }
    return Response(content=final_report, media_type="text/markdown; charset=utf-8", headers=headers)


@app.get("/api/sessions")
async def list_sessions_endpoint():
    """历史会话列表（按最近活跃排序）。"""
    return {"sessions": await sessions.list_sessions()}


@app.get("/api/sessions/{thread_id}")
async def get_session(thread_id: str):
    """加载某个历史会话的报告 + 来源（用于前端恢复查看 / 继续追问）。"""
    graph = get_graph()
    cfg = _cfg(thread_id)
    state = await graph.aget_state(cfg)
    if state is None:
        raise HTTPException(status_code=404, detail=f"thread {thread_id} not found")
    vals = state.values
    final_report = vals.get("final_report", "")
    sources = collect_citations(
        final_report,
        vals.get("short_url_map", []),
        vals.get("search_results", []),
    )
    return {
        "thread_id": thread_id,
        "title": vals.get("user_input", ""),
        "report": final_report,
        "sources": sources,
    }


@app.get("/api/report/export/{thread_id}")
async def export_report(thread_id: str, format: str = "docx"):
    """导出报告为 PDF 或 Word。"""
    graph = get_graph()
    cfg = _cfg(thread_id)
    state = await graph.aget_state(cfg)
    if state is None:
        raise HTTPException(status_code=404, detail=f"thread {thread_id} not found")
    vals = state.values
    final_report = vals.get("final_report", "")
    if not final_report:
        raise HTTPException(status_code=404, detail="该会话尚未生成最终报告")

    md = build_report_markdown(
        final_report,
        vals.get("short_url_map", []),
        vals.get("search_results", []),
    )
    user_input = vals.get("user_input", "research").strip()
    safe_name = "".join(
        c for c in user_input if c.isascii() and (c.isalnum() or c in " -_")
    )[:20] or "research"

    if format == "pdf":
        content = export_pdf(md)
        media = "application/pdf"
        filename = f"deepresearch_{safe_name}.pdf"
    else:
        content = export_docx(md)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"deepresearch_{safe_name}.docx"

    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    """就绪检查：图已编译、模型已配置、Tavily key 存在。"""
    checks: dict[str, object] = {}
    try:
        get_graph()
        checks["graph"] = "ok"
    except Exception:
        checks["graph"] = "not_initialized"
    try:
        checks["models_configured"] = len(get_model_factory().list_candidates())
    except Exception as exc:  # noqa: BLE001
        checks["models_configured"] = f"error: {exc}"
    checks["tavily_key"] = bool(settings.tavily.api_key and settings.tavily.api_key != "tvly-xxx")

    ready = (
        checks.get("graph") == "ok"
        and isinstance(checks.get("models_configured"), int)
        and int(checks["models_configured"]) > 0
        and bool(checks.get("tavily_key"))
    )
    return {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
        "uptime_seconds": round(uptime_seconds(), 1),
    }


@app.get("/metrics")
async def metrics():
    """Prometheus 文本格式的进程内指标。"""
    return Response(content=metrics_text(), media_type="text/plain; charset=utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.server.host, port=settings.server.port, reload=True)
