"""F8 质量评估体系（LLM-as-Judge）。"""
from .judge import DIMENSIONS, EvaluationResult, evaluate_report, interpret_band
from .judge_llm import get_eval_model_label, invoke_eval_llm, resolve_eval_model
from .evaluator import E2EResult, TopicCfg, run_e2e, run_e2e_single
from .html_report import render_html

__all__ = [
    "DIMENSIONS",
    "EvaluationResult",
    "evaluate_report",
    "interpret_band",
    "get_eval_model_label",
    "invoke_eval_llm",
    "resolve_eval_model",
    "E2EResult",
    "TopicCfg",
    "run_e2e",
    "run_e2e_single",
    "render_html",
]