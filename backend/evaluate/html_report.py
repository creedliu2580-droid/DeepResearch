"""E2E 评估结果 → 自包含 HTML 报告。

生成一个零外部依赖的 HTML 文件（内联 CSS/JS，无 CDN、无图片），
在浏览器中展示：汇总卡片、档位分布、主题维度对比表、逐主题明细。
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from evaluate.judge import DIMENSIONS, interpret_band
from evaluate.evaluator import E2EResult

# 维度展示名（顺序与 DIMENSIONS 一致）
_DIM_LABELS: dict[str, str] = {
    "factual_accuracy": "事实准确性",
    "information_coverage": "信息覆盖度",
    "logical_structure": "逻辑结构",
    "timeliness": "时效性",
    "citation_quality": "引用质量",
}

# 档位 → 色阶（用于背景/文字色）
_BAND_COLORS: dict[str, str] = {
    "优秀，接近人工撰写": "#16a34a",
    "合格，可用但可改进": "#0891b2",
    "较差，需调整配置": "#d97706",
    "不可用": "#dc2626",
}


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _score_color(score: float) -> str:
    """按 1–5 分返回色值（低=红，高=绿）。"""
    t = max(0.0, min(1.0, (score - 1) / 4))
    # 红(220,38,38) → 黄(234,179,8) → 绿(22,163,74) 三档插值
    if t < 0.5:
        r = 220 + (234 - 220) * (t / 0.5)
        g = 38 + (179 - 38) * (t / 0.5)
        b = 38 + (8 - 38) * (t / 0.5)
    else:
        r = 234 + (22 - 234) * ((t - 0.5) / 0.5)
        g = 179 + (163 - 179) * ((t - 0.5) / 0.5)
        b = 8 + (74 - 8) * ((t - 0.5) / 0.5)
    return f"rgb({int(r)},{int(g)},{int(b)})"


def _result_error(r: E2EResult) -> str | None:
    """统一判定一个主题是否失败，返回错误信息（成功返回 None）。

    口径同时覆盖：外层流水线错误（r.error）、Judge 内部解析失败（r.score.error）、
    以及 score 为 None 的兜底（避免前两者都为空时误判为成功）。
    """
    if r.error:
        return r.error
    if r.score is None:
        return "评估未完成（无评分结果）"
    if r.score.error:
        return r.score.error
    return None


def _scored(results: list[E2EResult]) -> list[E2EResult]:
    return [r for r in results if _result_error(r) is None]


def _stats(results: list[E2EResult]) -> dict[str, Any]:
    scored = _scored(results)
    bands: dict[str, int] = {}
    dim_totals: dict[str, float] = {d: 0.0 for d in DIMENSIONS}
    for r in scored:
        bands[r.score.band] = bands.get(r.score.band, 0) + 1
        for d in DIMENSIONS:
            dim_totals[d] += r.score.scores.get(d, {}).get("score", 0.0)
    n = len(scored)
    avg = round(sum(r.score.total_score for r in scored) / n, 2) if n else 0.0
    dim_avg = {d: round(v / n, 2) if n else 0.0 for d, v in dim_totals.items()}
    return {
        "total": len(results),
        "scored": n,
        "failed": len(results) - n,
        "avg": avg,
        "band": interpret_band(avg),
        "bands": bands,
        "dim_avg": dim_avg,
    }


def _summary_section(st: dict[str, Any]) -> str:
    """顶部汇总卡片。"""
    total = st["total"]
    cards = [
        ("主题数", st["total"], "#475569"),
        ("成功评估", st["scored"], "#16a34a"),
        ("失败", st["failed"], "#dc2626" if st["failed"] else "#94a3b8"),
    ]
    cards_html = "".join(
        f'<div class="card"><div class="card-num" style="color:{c}">{v}</div>'
        f'<div class="card-label">{k}</div></div>'
        for k, v, c in cards
    )

    # 平均分大数字
    avg_color = _BAND_COLORS.get(st["band"], "#475569")
    avg_html = (
        f'<div class="avg"><div class="avg-num" style="color:{avg_color}">{st["avg"]}</div>'
        f'<div class="avg-label">平均总分 / 5</div>'
        f'<div class="avg-band" style="background:{avg_color}">{st["band"]}</div></div>'
    )

    # 档位分布
    bars = ""
    for band, count in sorted(st["bands"].items(), key=lambda x: -x[1]):
        pct = (count / total * 100) if total else 0
        color = _BAND_COLORS.get(band, "#94a3b8")
        bars += (
            f'<div class="bar-row"><span class="bar-label">{_esc(band)}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div>'
            f'<span class="bar-count">{count}</span></div>'
        )
    dist_html = f'<div class="band-dist"><h3>档位分布</h3>{bars}</div>'

    return (
        f'<section class="summary">'
        f'<div class="summary-top">{cards_html}{avg_html}</div>'
        f'<div class="summary-bottom">{dist_html}{_dim_avg_section(st["dim_avg"])}</div>'
        f"</section>"
    )


def _dim_avg_section(dim_avg: dict[str, float]) -> str:
    """各维度平均分条形图。"""
    rows = ""
    for d in DIMENSIONS:
        v = dim_avg.get(d, 0.0)
        rows += (
            f'<div class="bar-row"><span class="bar-label">{_DIM_LABELS[d]}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{v / 5 * 100:.1f}%;'
            f'background:{_score_color(v)}"></div></div>'
            f'<span class="bar-count">{v}</span></div>'
        )
    return f'<div class="dim-avg"><h3>各维度均值</h3>{rows}</div>'


def _comparison_table(results: list[E2EResult]) -> str:
    """主题 × 维度 对比表（热力色块）。"""
    scored = _scored(results)
    if not scored:
        return '<section class="table-wrap"><p class="empty">无成功评估结果</p></section>'
    scored = sorted(scored, key=lambda r: -r.score.total_score)

    head = "".join(f"<th>{_DIM_LABELS[d]}</th>" for d in DIMENSIONS)
    rows = ""
    for r in scored:
        cells = ""
        for d in DIMENSIONS:
            s = r.score.scores.get(d, {}).get("score", 0.0)
            cells += (
                f'<td><span class="chip" style="background:{_score_color(s)}">{s:.0f}</span></td>'
            )
        rows += (
            f'<tr><td class="topic-cell" title="{_esc(r.topic)}">{_esc(_short(r.topic, 40))}</td>'
            f'{cells}<td class="total-cell"><b>{r.score.total_score}</b></td></tr>'
        )

    return (
        '<section class="table-wrap"><h3>主题 × 维度 对比</h3>'
        '<div class="table-scroll"><table><thead><tr><th>主题</th>'
        f'{head}<th>总分</th></tr></thead><tbody>{rows}</tbody></table></div></section>'
    )


def _detail_sections(results: list[E2EResult]) -> str:
    """逐主题明细卡。"""
    cards = ""
    for r in sorted(
        results,
        key=lambda x: -(x.score.total_score if _result_error(x) is None else -1),
    ):
        cards += _detail_card(r)
    return f'<section class="details"><h3>逐主题明细</h3>{cards}</section>'


def _detail_card(r: E2EResult) -> str:
    err = _result_error(r)
    if err:
        return (
            f'<article class="detail error"><header><h4>{_esc(r.topic)}</h4>'
            f'<span class="badge" style="background:#dc2626">失败</span></header>'
            f'<p class="err-msg">{_esc(err)}</p></article>'
        )

    s = r.score
    band_color = _BAND_COLORS.get(s.band, "#94a3b8")

    dims = ""
    for d in DIMENSIONS:
        v = s.scores.get(d, {})
        score = v.get("score", 0.0)
        reason = v.get("reason", "")
        dims += (
            f'<div class="dim-row">'
            f'<span class="dim-name">{_DIM_LABELS[d]}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{score / 5 * 100:.1f}%;'
            f'background:{_score_color(score)}"></div></div>'
            f'<span class="dim-score">{score:.0f}</span>'
            f'<span class="dim-reason">{_esc(reason)}</span>'
            f"</div>"
        )

    return (
        f'<article class="detail"><header>'
        f'<h4 title="{_esc(r.topic)}">{_esc(_short(r.topic, 60))}</h4>'
        f'<span class="badge" style="background:{band_color}">{s.total_score} 分 · {_esc(s.band)}</span>'
        f"</header>"
        f'<div class="meta">effort={_esc(r.effort)} · 来源 {len(r.sources)} 条 · 报告 {len(r.report)} 字</div>'
        f'<div class="dims">{dims}</div>'
        f'<p class="summary-text">{_esc(s.summary)}</p>'
        f"</article>"
    )


def _short(text: str, n: int) -> str:
    t = text.strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def render_html(results: list[E2EResult], title: str = "DeepResearch E2E 评估报告") -> str:
    """把评估结果渲染为完整自包含 HTML 字符串。"""
    st = _stats(results)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f1f5f9; color: #1e293b; line-height: 1.5; }}
.page {{ max-width: 1100px; margin: 0 auto; padding: 32px 20px 64px; }}
h1 {{ font-size: 24px; margin-bottom: 4px; }}
.sub {{ color: #64748b; font-size: 13px; margin-bottom: 20px; }}
h3 {{ font-size: 16px; margin: 24px 0 12px; color: #0f172a; }}
.summary {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
.summary-top {{ display: flex; gap: 20px; align-items: stretch; flex-wrap: wrap; }}
.card {{ flex: 1; min-width: 90px; background: #f8fafc; border-radius: 8px; padding: 14px; text-align: center; }}
.card-num {{ font-size: 28px; font-weight: 700; }}
.card-label {{ font-size: 12px; color: #64748b; }}
.avg {{ flex: 1.3; min-width: 160px; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #f8fafc; border-radius: 8px; padding: 14px; }}
.avg-num {{ font-size: 40px; font-weight: 800; line-height: 1; }}
.avg-label {{ font-size: 12px; color: #64748b; margin: 4px 0 8px; }}
.avg-band {{ color: #fff; font-size: 12px; padding: 3px 12px; border-radius: 99px; }}
.summary-bottom {{ display: flex; gap: 24px; margin-top: 20px; flex-wrap: wrap; }}
.band-dist, .dim-avg {{ flex: 1; min-width: 280px; }}
.bar-row {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
.bar-label {{ width: 90px; font-size: 12px; color: #475569; text-align: right; flex-shrink: 0; }}
.bar-track {{ flex: 1; height: 12px; background: #e2e8f0; border-radius: 6px; overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 6px; }}
.bar-count {{ width: 34px; font-size: 12px; color: #334155; font-weight: 600; }}
.table-wrap {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); margin-top: 16px; }}
.table-wrap h3 {{ margin-top: 0; }}
.table-scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ padding: 8px 10px; text-align: center; border-bottom: 1px solid #eef2f7; }}
th {{ background: #f8fafc; color: #475569; font-weight: 600; white-space: nowrap; }}
.topic-cell {{ text-align: left; max-width: 260px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.total-cell {{ font-size: 15px; }}
.chip {{ display: inline-block; color: #fff; font-weight: 700; width: 26px; height: 26px; line-height: 26px; border-radius: 6px; }}
.details {{ margin-top: 16px; }}
.detail {{ background: #fff; border-radius: 12px; padding: 18px 20px; margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
.detail header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }}
.detail h4 {{ font-size: 15px; }}
.badge {{ color: #fff; font-size: 12px; padding: 4px 10px; border-radius: 99px; white-space: nowrap; }}
.meta {{ color: #94a3b8; font-size: 12px; margin: 6px 0 14px; }}
.dims {{ display: flex; flex-direction: column; gap: 4px; }}
.dim-row {{ display: flex; align-items: center; gap: 8px; }}
.dim-name {{ width: 80px; font-size: 12px; color: #475569; flex-shrink: 0; }}
.dim-score {{ width: 20px; font-size: 13px; font-weight: 700; text-align: center; }}
.dim-reason {{ color: #94a3b8; font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.summary-text {{ color: #475569; font-size: 13px; border-top: 1px dashed #e2e8f0; padding-top: 10px; }}
.err-msg {{ color: #dc2626; font-size: 13px; margin-top: 8px; }}
.empty {{ color: #94a3b8; font-size: 14px; }}
</style>
</head>
<body>
<div class="page">
<h1>{_esc(title)}</h1>
<div class="sub">生成时间：{ts} · 共 {st['total']} 个主题</div>
{_summary_section(st)}
{_comparison_table(results)}
{_detail_sections(results)}
</div>
</body>
</html>"""