"""工具函数自测：JSON 解析容错 + 来源去重（无需 LLM key，纯函数）。

运行：
    python tests/test_utils.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils import parse_json, parse_json_list
from app.citations import (
    collect_citations,
    dedupe_search_results,
    make_short_url,
    resolve_citation_markers,
    restore_urls,
    sources_lines,
)


def test_parse_json_fenced():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('```\n["x", "y"]\n```') == ["x", "y"]


def test_parse_json_with_noise():
    # 前后有说明文字，只提取首对 {...}
    assert parse_json('这是结果：{"a": 1} 完毕') == {"a": 1}


def test_parse_json_list():
    assert parse_json_list('```json\n["a", "b", "c"]\n```', cap=2) == ["a", "b"]


def test_parse_json_list_line_fallback():
    # 非 JSON 文本按行回退，去掉列表符号与序号
    assert parse_json_list("- 苹果\n- 香蕉\n3. 橙子") == ["苹果", "香蕉", "橙子"]


def test_dedupe_search_results():
    rows = [
        {"title": "A", "url": "http://a"},
        {"title": "A", "url": "http://a"},  # 重复
        {"title": "", "url": "http://x"},   # 空 title 剔除
        {"title": "B", "url": "http://b"},
    ]
    out = dedupe_search_results(rows)
    assert [r["title"] for r in out] == ["A", "B"]


def test_sources_lines():
    rows = [{"title": "A", "url": "http://a", "query": "q1"}]
    assert "[q1] A | A | 引用写法 [1]" in sources_lines(rows)
    assert "q1" not in sources_lines(rows, include_query=False)


def test_sources_lines_uses_short_url():
    rows = [{"title": "A", "url": "http://a", "short_url": "https://search.com/id/1-0-0"}]
    assert "[A](https://search.com/id/1-0-0)" in sources_lines(rows)
    assert "http://a" not in sources_lines(rows)  # 不暴露真实 URL，用短链


def test_resolve_citation_markers():
    rows = [
        {"title": "搜狐新闻", "url": "https://www.sohu.com/a/1"},
        {"title": "百度文章", "url": "https://intl.cloud.baidu.com/x"},
    ]
    text = "结论A [1]，结论B [1,2]，未知 [9]。"
    out = resolve_citation_markers(text, rows)
    assert "[搜狐](https://www.sohu.com/a/1)" in out
    assert "[百度](https://intl.cloud.baidu.com/x)" in out
    assert "[9]" in out  # 未知编号保留


def test_make_short_url():
    assert make_short_url(1, 0, 0) == "https://search.com/id/1-0-0"
    assert make_short_url(2, 3, 4) == "https://search.com/id/2-3-4"


def test_restore_urls():
    text = "正文 [来源](https://search.com/id/1-0-0) 和裸链 https://search.com/id/1-0-1。"
    url_map = [
        {"short": "https://search.com/id/1-0-0", "real": "https://example.com/a"},
        {"short": "https://search.com/id/1-0-1", "real": "https://example.com/b"},
    ]
    out = restore_urls(text, url_map)
    assert "https://example.com/a" in out
    assert "https://example.com/b" in out
    assert "search.com/id/" not in out


def test_collect_citations_only_real_sources():
    text = (
        "见 [来源：示例标题](https://example.com/a) 与 https://example.com/b，"
        "以及一个编造的 https://fake.com/x。"
    )
    url_map = [
        {"short": "https://search.com/id/1-0-0", "real": "https://example.com/a"},
        {"short": "https://search.com/id/1-0-1", "real": "https://example.com/b"},
    ]
    search_results = [
        {"title": "示例标题", "url": "https://example.com/a"},
        {"title": "另一来源", "url": "https://example.com/b"},
    ]
    cites = collect_citations(text, url_map, search_results)
    urls = sorted(c["url"] for c in cites)
    assert urls == ["https://example.com/a", "https://example.com/b"]  # fake.com/x 被排除
    titles = {c["url"]: c["title"] for c in cites}
    assert titles["https://example.com/a"] == "示例标题"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("全部通过")
