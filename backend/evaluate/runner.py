"""F8 质量评估 CLI —— 从 test_set.json 加载主题，从头跑 agent 流水线做 E2E 评估。

用法:
  # 跑测试集全部主题（真实消耗 Tavily + LLM 配额）
  python -m evaluate.runner

  # 单主题
  python -m evaluate.runner --topic "你的研究主题"

  # 指定 judge 模型 / 输出文件
  python -m evaluate.runner --judge-model deepseek-flash --out result.json

  # 独立评估模型（与主程序解耦，见 judge_llm.py）
  #   优先级: --judge-model > EVAL_MODEL > EVAL_BASE_URL+EVAL_API_KEY > review 默认
  #   换厂商时在 .env 配:
  #     EVAL_MODEL=gpt-4o
  #     EVAL_BASE_URL=https://api.openai.com/v1
  #     EVAL_API_KEY=sk-xxx

组件级评估暂未实现（PRD F8 先做 E2E，组件级后续扩展）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluate.evaluator import (  # noqa: E402
    E2EResult,
    TopicCfg,
    run_e2e,
)
from evaluate.html_report import render_html  # noqa: E402
from evaluate.judge import DIMENSIONS  # noqa: E402

_TEST_SET_PATH = Path(__file__).resolve().parent / "test_set.json"


def _load_test_set(path: Path) -> list[TopicCfg]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [TopicCfg.from_dict(item) for item in data.get("topics", [])]


def _error_of(r: E2EResult) -> str | None:
    """统一失败判定（与 html_report._result_error 口径一致）。"""
    if r.error:
        return r.error
    if r.score is None:
        return "评估未完成（无评分结果）"
    if r.score.error:
        return r.score.error
    return None


_DIM_LABELS = {
    "factual_accuracy": "事实准确性",
    "information_coverage": "信息覆盖度",
    "logical_structure": "逻辑结构",
    "timeliness": "时效性",
    "citation_quality": "引用质量",
}


def _display_width(s: str) -> int:
    """按终端显示宽度计算（中文全角算 2，其余算 1）。"""
    return sum(2 if ord(ch) > 127 else 1 for ch in s)


def _aligned_line(label: str, value: str) -> str:
    """中文标签 + 冒号后补空格，使 value 对齐到统一列（参考项目排版）。"""
    pad = 12 - (_display_width(label) + 1)
    if pad < 1:
        pad = 1
    return f"    {label}:" + " " * pad + value


def _print_summary(results: list[E2EResult]) -> None:
    scored = [r for r in results if _error_of(r) is None]
    errors = [r for r in results if _error_of(r) is not None]

    print()
    print("=" * 72)
    print("  DeepResearch Agent 评估报告")
    print("=" * 72)
    print()
    print("--- 端到端报告得分 ---")
    print()

    for r in scored:
        s = r.score
        print(f"  主题: {r.topic}")
        print(f"    总评分: {s.total_score:.1f}/5")
        for dim in DIMENSIONS:
            label = _DIM_LABELS.get(dim, dim)
            entry = s.scores.get(dim) or {}
            score = entry.get("score", 0)
            print(_aligned_line(label, f"{score:.0f}/5"))
        print()

    if scored:
        avg = sum(r.score.total_score for r in scored) / len(scored)
        print(f"  ** 平均总评分: {avg:.1f}/5 (n={len(scored)}) **")
        print()

    if errors:
        print("--- 失败明细 ---")
        for r in errors:
            print(f"  [{r.topic[:60]}] {_error_of(r)}")


def _save(results: list[E2EResult], path: Path) -> None:
    payload = {
        "timestamp": datetime.now().isoformat(),
        "dimensions": list(DIMENSIONS),
        "results": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {path}")


def main() -> None:
    from app.observability import setup_logging

    setup_logging(level=logging.INFO)
    p = argparse.ArgumentParser(description="DeepResearch 端到端质量评估（LLM-as-Judge，PRD F8）")
    p.add_argument("--topic", help="单个研究主题（覆盖测试集）")
    p.add_argument("--test-set", type=Path, default=_TEST_SET_PATH, help="测试集 JSON 路径")
    p.add_argument(
        "--judge-model",
        default="",
        help="Judge 所用模型（独立通道，优先级: --judge-model > EVAL_MODEL > EVAL_BASE_URL+EVAL_API_KEY > review 默认）",
    )
    p.add_argument("--agent-model", default="", help="跑流水线所用模型（空则走各角色默认）")
    p.add_argument("--out", type=Path, help="结果输出 JSON 路径（可选）")
    p.add_argument("--html", type=Path, help="结果输出 HTML 报告路径（可选）")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="单主题模式下的研究深度")
    args = p.parse_args()

    judge_model = args.judge_model.strip() or None
    agent_model = args.agent_model.strip() or None

    if args.topic:
        cfg = TopicCfg(topic=args.topic, effort=args.effort or "medium")
        topics = [cfg]
    else:
        if not args.test_set.exists():
            p.error(f"测试集不存在: {args.test_set}")
        topics = _load_test_set(args.test_set)

    print(f"评估模式: E2E（从头跑 agent 流水线）")
    print(f"主题数: {len(topics)}")
    for c in topics:
        print(f"  - {c.topic[:80]}  (effort={c.effort})")
    print()

    results = asyncio.run(run_e2e(topics, judge_model=judge_model, agent_model=agent_model))

    _print_summary(results)

    if args.out:
        _save(results, args.out)

    if args.html:
        args.html.write_text(render_html(results), encoding="utf-8")
        print(f"\nHTML 报告已写入 {args.html}")


if __name__ == "__main__":
    main()