"""
搜索工具抽象层 — ABC 定义 + Tavily 实现（支持异步并行）。

改造（2026-10-29）：加 asearch / asearch_batch 异步接口，
让 search_node 可以 asyncio.gather 并行多 query。
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..settings import get_settings
from ..limits import get_search_semaphore
from ..observability import record_search

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """标准化搜索结果 — 屏蔽不同供应商的返回差异。"""
    query: str
    title: str
    url: str
    content: str

    def to_dict(self) -> dict:
        return self.__dict__


class SearchTool(ABC):
    """搜索工具接口 — 同步 + 异步两套方法，实现类按需选择。"""

    # ---- 同步（保留兼容） ----
    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        ...

    def search_batch(self, queries: list[str], max_results: int = 5) -> list[list[SearchResult]]:
        """同步批量搜索 — 返回与 queries 一一对应的结果列表。"""
        return [self.search(q, max_results) for q in queries]

    # ---- 异步（并行友好） ----
    @abstractmethod
    async def asearch(self, query: str, max_results: int = 5) -> list[SearchResult]:
        ...

    async def asearch_batch(self, queries: list[str], max_results: int | None = None) -> list[list[SearchResult]]:
        """并行批量搜索 — 默认实现用 asyncio.gather。

        返回与 queries 一一对应的结果列表（list[list[SearchResult]]），
        调用方据此生成 `round-query-result` 短链。
        单个 query 失败不影响其它：失败项对应空列表；全部失败才抛异常，
        让调用方（search_node）能看到真实错误（如缺 key）而非静默吞掉。
        """
        tasks = [self.asearch(q, max_results) for q in queries]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[list[SearchResult]] = []
        errors: list[BaseException] = []
        for batch in batches:
            if isinstance(batch, BaseException):
                errors.append(batch)
                results.append([])
            else:
                results.append(batch)
        if errors and len(errors) == len(queries):
            raise errors[0]
        return results


class TavilySearchTool(SearchTool):
    """Tavily 实现 — 原生支持异步。"""

    def __init__(self, api_key: str | None = None) -> None:
        from tavily import TavilyClient, AsyncTavilyClient

        settings = get_settings()
        self._sync_client = TavilyClient(api_key=api_key or settings.tavily.api_key)
        self._async_client = AsyncTavilyClient(api_key=api_key or settings.tavily.api_key)
        self._max_results = settings.tavily.max_results
        self._depth = settings.tavily.search_depth

    # ---- 同步 ----
    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        limit = max_results or self._max_results
        resp = self._sync_client.search(
            query=query,
            max_results=limit,
            search_depth=self._depth,
            include_answer=False,
        )
        return self._parse(query, resp.get("results", []))

    # ---- 异步 ----
    async def asearch(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        limit = max_results or self._max_results
        t0 = time.monotonic()
        try:
            async with get_search_semaphore():
                resp = await self._async_client.search(
                    query=query,
                    max_results=limit,
                    search_depth=self._depth,
                    include_answer=False,
                )
            dur = time.monotonic() - t0
            record_search(True, dur)
            logger.info("搜索耗时 %.1fs", dur)
            return self._parse(query, resp.get("results", []))
        except Exception as exc:
            record_search(False, time.monotonic() - t0)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 432:
                raise RuntimeError(
                    "Tavily 搜索额度已用尽（HTTP 432）：请更换或充值 API key，或降低搜索量"
                ) from exc
            if status == 429:
                raise RuntimeError(
                    "Tavily 请求过于频繁（HTTP 429）：请稍后重试或降低并发"
                ) from exc
            if status == 401:
                raise RuntimeError(
                    "Tavily API key 无效（HTTP 401）：请检查 .env 里的 TAVILY_API_KEY"
                ) from exc
            raise

    @staticmethod
    def _parse(query: str, raw: list[dict]) -> list[SearchResult]:
        return [
            SearchResult(
                query=query,
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
            )
            for r in raw
        ]


def build_search_tool() -> SearchTool:
    """工厂函数 — 根据配置返回具体实现。"""
    return TavilySearchTool()
