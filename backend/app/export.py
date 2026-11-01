"""报告导出：Markdown → Word (docx) / PDF。"""
from __future__ import annotations

import io
import re

from .citations import strip_source_sections, append_sources_section, collect_citations


def build_report_markdown(
    final_report: str,
    short_url_map: list[dict],
    search_results: list[dict],
) -> str:
    """生成最终可导出的 Markdown：剥掉来源章节 + 补统一「信息来源」。"""
    sources = collect_citations(final_report, short_url_map, search_results)
    return append_sources_section(strip_source_sections(final_report), sources)


# ---------- Word ----------

_INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _plain(text: str) -> str:
    """去掉行内 markdown 语法：链接 → 文字、**bold**/`code` → 纯文本。"""
    text = _INLINE_LINK_RE.sub(r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


def export_docx(md: str) -> bytes:
    from docx import Document

    doc = Document()
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 4)
            doc.add_heading(_plain(m.group(2)), level=level)
            i += 1
            continue

        # 无序列表
        if re.match(r"^[-*+]\s+", stripped):
            doc.add_paragraph(_plain(stripped[2:]), style="List Bullet")
            i += 1
            continue

        # 有序列表
        if re.match(r"^\d+\.\s+", stripped):
            doc.add_paragraph(_plain(re.sub(r"^\d+\.\s*", "", stripped)), style="List Number")
            i += 1
            continue

        # 表格（连续 | 行）
        if stripped.startswith("|") and stripped.endswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                if re.match(r"^\|[\s:\-|]+\|$", row):  # 分隔行
                    i += 1
                    continue
                cells = [_plain(c) for c in row.strip("|").split("|")]
                rows.append(cells)
                i += 1
            if rows:
                ncols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=ncols)
                table.style = "Table Grid"
                for ri, row in enumerate(rows):
                    for ci in range(ncols):
                        table.cell(ri, ci).text = row[ci] if ci < len(row) else ""
                doc.add_paragraph()
            continue

        # 代码块（```）
        if stripped.startswith("```"):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 跳过结尾 ```
            if code_lines:
                p = doc.add_paragraph()
                run = p.add_run("\n".join(code_lines))
                run.font.name = "Consolas"
            continue

        # 普通段落
        doc.add_paragraph(_plain(stripped))
        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- PDF ----------

def export_pdf(md: str) -> bytes:
    import markdown
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from xhtml2pdf import pisa

    # 注册内置中文 CID 字体，否则 xhtml2pdf 默认 Helvetica 会把中文渲染成空白
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:
        pass

    html = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    wrapper = (
        "<html><head><meta charset='utf-8'>"
        "<style>body{font-family:'STSong-Light';font-size:10.5pt;line-height:1.6;}"
        "table{border-collapse:collapse;width:100%;}"
        "td,th{border:1px solid #999;padding:4px;} pre{background:#f5f5f5;padding:6px;}"
        "a{color:#2563eb;text-decoration:none;}</style></head><body>"
        + html
        + "</body></html>"
    )
    buf = io.BytesIO()
    pisa.CreatePDF(wrapper, dest=buf, encoding="utf-8")
    return buf.getvalue()
