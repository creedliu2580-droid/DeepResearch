"""引用与来源处理：去重、格式化、短链还原、终稿来源提取。

设计要点：
- 搜索阶段给每条结果分配 `https://search.com/id/{round}-{query}-{result}` 短链，LLM 写报告时只需
  抄写短链，从机制上避免编造/截断真实 URL；
- 润色完成后用 `restore_urls` 把短链还原成真实 URL，产出权威终稿；
- `collect_citations` 从终稿正文里提取「实际被引用」的来源，且只接受确实
  来自搜索结果的 URL（杜绝幻觉来源），按 URL 去重。
"""

from __future__ import annotations

import re
from typing import Any

_SHORT_URL_RE = re.compile(r"https://search\.com/id/\d+-\d+-\d+")
_SECTION_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*?)\s*$")


def make_short_url(round_no: int, query_idx: int, result_idx: int) -> str:
    """按「轮次-查询-结果」三元组生成短链，可读且确定性唯一。"""
    return f"https://search.com/id/{round_no}-{query_idx}-{result_idx}"


def dedupe_search_results(search_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (title, url) 去重，剔除 title/url 为空的条目，保持首次出现顺序。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in search_results or []:
        title = r.get("title", "") or ""
        url = r.get("url", "") or ""
        key = (title, url)
        if key in seen or not title or not url:
            continue
        seen.add(key)
        out.append(r)
    return out


def sources_lines(
    sources: list[dict[str, Any]],
    max_items: int = 30,
    include_query: bool = True,
) -> str:
    """把去重后的来源列表格式化为精简文本，供 draft/polish 的 prompt 使用。

    每条给 LLM：「来源简称 + 标题 + 内容摘要 + 引用写法 [来源名](短链URL)」。
    LLM 在正文里直接抄短链 URL 引用，真实链接由 restore_urls 用确定性代码还原，
    从根本上避免「编号↔事实」错配 / 让 LLM 抄长 URL 产生幻觉。

    Args:
        include_query: 是否在每行前置 [查询词]。draft 需要（帮 LLM 对齐来源与问题），
                       polish 不需要。
    """
    lines: list[str] = []
    for i, r in enumerate(sources[:max_items], 1):
        title = r.get("title", "") or ""
        query = r.get("query", "") or ""
        name = r.get("source_name") or _source_name_from_url(r.get("url", "") or "")
        short = r.get("short_url", "") or ""
        content = (r.get("content", "") or "").replace("\n", " ").strip()
        if len(content) > 400:
            content = content[:400] + "…"
        prefix = f"[{query}] " if (include_query and query) else ""
        cite = f"[{name}]({short})" if short else f"[{i}]"
        line = f"  {i}. {prefix}{name} | {title}"
        if content:
            line += f" | 内容: {content}"
        line += f" | 引用写法 {cite}"
        lines.append(line)
    return "\n".join(lines) if lines else "（无来源）"


_CITATION_MARKER_RE = re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\](?!\()")


def resolve_citation_markers(text: str, sources: list[dict[str, Any]]) -> str:
    """把正文里的 `[N]` / `[N,M]` 编号标注还原为 `[来源名](真实链接)`。

    用确定性代码做映射：LLM 只写编号（如 [1]、[2,3]），真实链接和中文简称在这里
    按来源列表顺序填充，从根本上避免 LLM 抄长 URL 的幻觉与 token 浪费。
    未知编号原样保留，避免误伤正文里的其它方括号内容。
    """
    id_to_source = {i: r for i, r in enumerate(sources or [], 1)}

    def repl(m: re.Match) -> str:
        parts: list[str] = []
        for token in re.split(r"[,，、]", m.group(1)):
            token = token.strip()
            if not token.isdigit():
                return m.group(0)
            r = id_to_source.get(int(token))
            if not r:
                return m.group(0)
            name = r.get("source_name") or _source_name_from_url(r.get("url", "") or "")
            url = r.get("url", "") or ""
            parts.append(f"[{name}]({url})")
        return "".join(parts)

    return _CITATION_MARKER_RE.sub(repl, text)


def restore_urls(text: str, url_map: list[dict[str, str]]) -> str:
    """把正文里出现的所有短链还原成真实 URL（无论是否在 Markdown 链接内）。"""
    short_to_real = {
        e.get("short", ""): e.get("real", e.get("short", "")) for e in url_map
    }
    return _SHORT_URL_RE.sub(lambda m: short_to_real.get(m.group(0), m.group(0)), text)


def _clean_label(label: str) -> str:
    """去掉 `来源：` / `来源:` 前缀，得到更干净的可展示来源名。"""
    return re.sub(r"^来源[:：]\s*", "", (label or "").strip()).strip()


# 常见中文站点：域名 → 中文名（避免显示拼音，如 sohu.com → 搜狐）
_CHINESE_SITE_NAMES: dict[str, str] = {
    "sohu.com": "搜狐",
    "baidu.com": "百度",
    "zhihu.com": "知乎",
    "weibo.com": "微博",
    "sina.com": "新浪",
    "sina.com.cn": "新浪",
    "qq.com": "腾讯",
    "163.com": "网易",
    "126.com": "网易",
    "bilibili.com": "哔哩哔哩",
    "36kr.com": "36氪",
    "ifeng.com": "凤凰网",
    "huxiu.com": "虎嗅",
    "jiemian.com": "界面新闻",
    "thepaper.cn": "澎湃新闻",
    "huanqiu.com": "环球网",
    "people.com.cn": "人民网",
    "xinhuanet.com": "新华网",
    "caixin.com": "财新",
    "yicai.com": "第一财经",
    "eastmoney.com": "东方财富",
    "investing.com": "英为财情",
    "marketresearchfuture.com": "MRFR",
    "csdn.net": "CSDN",
    "cnblogs.com": "博客园",
    "oschina.net": "开源中国",
    "juejin.cn": "掘金",
    "v2ex.com": "V2EX",
    "douban.com": "豆瓣",
    "toutiao.com": "今日头条",
    "bytedance.com": "字节跳动",
    "jd.com": "京东",
    "taobao.com": "淘宝",
    "tmall.com": "天猫",
    "xiaomi.com": "小米",
    "huawei.com": "华为",
    "ithome.com": "IT之家",
    "leiphone.com": "雷峰网",
    "geekpark.net": "极客公园",
    "pingwest.com": "品玩",
    "jiqizhixin.com": "机器之心",
    # 政府 / 部委 / 官方数据（市场分析高频来源）
    "gov.cn": "中国政府网",
    "stats.gov.cn": "国家统计局",
    "mofcom.gov.cn": "商务部",
    "miit.gov.cn": "工业和信息化部",
    "mof.gov.cn": "财政部",
    "ndrc.gov.cn": "国家发展改革委",
    "pboc.gov.cn": "中国人民银行",
    "nmpa.gov.cn": "国家药监局",
    "nhc.gov.cn": "国家卫健委",
    "gacc.gov.cn": "海关总署",
    "samr.gov.cn": "市场监管总局",
    # 行业研究 / 咨询数据平台
    "chyxx.com": "智研咨询",
    "qianzhan.com": "前瞻产业研究院",
    "iimedia.cn": "艾媒咨询",
    "askci.com": "中商产业研究院",
    "199it.com": "199IT",
    "iresearch.cn": "艾瑞咨询",
    "cbndata.com": "CBNData",
    # 国际组织（宏观数据常引）
    "un.org": "联合国",
    "who.int": "世界卫生组织",
    "worldbank.org": "世界银行",
    "imf.org": "国际货币基金组织",
    "oecd.org": "OECD",
}


def _source_name_from_url(url: str) -> str:
    """从 URL 域名推导一个简洁来源名。

    优先匹配常见中文站点映射（显示中文名，如 sohu.com → 搜狐）；
    否则取主域名首字母大写（如 example.com → Example）。
    """
    try:
        host = url.split("//")[-1].split("/")[0].split("?")[0].split(":")[0]
        host = host.replace("www.", "").strip(".").lower()
        if not host:
            return "来源"
        # 匹配最长的已知域名后缀（兼容子域名 / 二级域名 .com.cn 等）
        best, best_name = "", ""
        for domain, name in _CHINESE_SITE_NAMES.items():
            if (host == domain or host.endswith("." + domain)) and len(domain) > len(best):
                best, best_name = domain, name
        if best_name:
            return best_name
        labels = [l for l in host.split(".") if l]
        name = labels[-2] if len(labels) >= 2 else (labels[0] if labels else "来源")
        return name[:1].upper() + name[1:]
    except Exception:
        return "来源"


def _strip_trailing_punct(url: str) -> str:
    """剥掉裸 URL 结尾被顺带匹配进来的标点（中英文）。"""
    return re.sub(r"[.,;:!?，。、；：！？\)\]]+$", "", url)


def collect_citations(
    text: str,
    url_map: list[dict[str, str]],
    search_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """从终稿正文提取「实际被引用」的来源，按 URL 去重。

    只接受确实来自搜索结果的 URL（通过 url_map 或 search_results 校验），
    因此 LLM 即使凭空写出一个看似合理的 URL，也不会被算作来源。
    """
    short_to_real = {
        e.get("short", ""): e.get("real", e.get("short", "")) for e in url_map
    }

    known: set[str] = {r.get("url", "") for r in (search_results or []) if r.get("url")}
    known |= set(short_to_real.values())
    known.discard("")

    # 真实 URL → 干净标题（来自搜索结果，优先）
    real_to_title: dict[str, str] = {}
    for r in search_results or []:
        url = r.get("url", "")
        title = r.get("title", "")
        if url and title and url not in real_to_title:
            real_to_title[url] = title

    seen: dict[str, dict[str, str]] = {}

    def add(label: str, url: str) -> None:
        real = short_to_real.get(url, url)
        if not real.startswith("http") or real in seen or real not in known:
            return
        title = real_to_title.get(real) or _clean_label(label) or _source_name_from_url(real)
        seen[real] = {"title": title, "url": real}

    # 1. Markdown 内联 `[标签](url)`
    for m in re.finditer(r"\[([^\]]*)\]\((https?://[^)]*)\)", text):
        add(m.group(1), m.group(2))

    # 2. 裸 URL（排除紧跟 "(" 之后的，即已是内联链接 URL 的部分），并剥掉尾部标点
    #    显式 URL 安全字符集，避免把 URL 后的中文正文吞进链接
    for m in re.finditer(r"(?<!\()https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]]+", text):
        add("", _strip_trailing_punct(m.group(0)))

    return list(seen.values())


def strip_source_sections(text: str) -> str:
    """删除报告中所有「来源」类 Markdown 章节（来源列表 / 信息来源等）。

    LLM 可能把 prompt 里的来源列表章节原样抄进正文，导致终稿出现重复/错误的来源章节。
    这里把它们整段剥掉，只保留正文里的内联 `[来源名](url)` 标注；统一的
    「信息来源」章节由前端/接口用真实来源（collect_citations）单独生成。
    """
    lines = (text or "").split("\n")
    out: list[str] = []
    skipping = False
    skip_level = 0
    for line in lines:
        m = _SECTION_HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            heading = m.group(2).strip()
            if "来源列表" in heading or "信息来源" in heading or heading == "来源":
                skipping = True
                skip_level = level
                continue
            if skipping and level <= skip_level:
                skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out).strip()


def append_sources_section(text: str, sources: list[dict[str, str]]) -> str:
    """在报告末尾追加统一的「## 信息来源」章节（真实来源）。"""
    if not sources:
        return (text or "").strip()
    lines = []
    for i, s in enumerate(sources, 1):
        title = s.get("title") or _source_name_from_url(s.get("url", ""))
        lines.append(f"{i}. [{title}]({s.get('url', '')})")
    body = (text or "").strip()
    return body + "\n\n## 信息来源\n\n" + "\n".join(lines) + "\n"


# 年份 / 百分比 / 万、亿（用于生成后的事实核对兜底）
_NUM_RE = re.compile(r"(\d{4})\s*年|(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*[万亿]")


def check_number_grounding(text: str, reference: str) -> list[str]:
    """提取报告里的数字（年份/百分比/万/亿），返回不在参考文本中的（疑似无出处）。

    用于生成后的事实核对兜底：如果一个具体数字在 research_summary 里找不到，
    很可能是模型编造的，标记出来供告警。启发式，会有少量误报（如推导出的数字）。
    """
    if not reference:
        return []
    flagged: list[str] = []
    seen: set[str] = set()
    for m in _NUM_RE.finditer(text or ""):
        core = next(g for g in m.groups() if g is not None)
        if core in seen:
            continue
        seen.add(core)
        if core not in reference:
            flagged.append(m.group(0))
    return flagged
