"""轻量可观测性：进程内指标 + 结构化日志 + 健康检查辅助。

单机够用；多实例/分布式请对接 Prometheus（抓取 /metrics）、日志采集（ELK/Loki）
或 OpenTelemetry，本模块只提供基础的进程内计数与 JSON 日志。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_lock = threading.Lock()
_counters: dict[str, float] = defaultdict(float)
_started_at = time.time()


# ---------- 指标 ----------

def _incr(name: str, n: float = 1.0) -> None:
    with _lock:
        _counters[name] += n


def record_http(method: str, path: str, status: int, duration_s: float) -> None:
    _incr("http_requests_total")
    _incr("http_request_duration_seconds_sum", duration_s)
    _incr("http_request_duration_seconds_count")
    if status >= 400:
        _incr("http_errors_total")


def record_llm(role: str, success: bool, duration_s: float) -> None:
    _incr("llm_calls_total")
    _incr(f"llm_calls_total_role_{role}")
    _incr("llm_duration_seconds_sum", duration_s)
    _incr("llm_duration_seconds_count")
    if not success:
        _incr("llm_errors_total")


def record_search(success: bool, duration_s: float) -> None:
    _incr("search_calls_total")
    _incr("search_duration_seconds_sum", duration_s)
    _incr("search_duration_seconds_count")
    if not success:
        _incr("search_errors_total")


def snapshot() -> dict[str, float]:
    with _lock:
        return dict(_counters)


def uptime_seconds() -> float:
    return time.time() - _started_at


def metrics_text() -> str:
    """Prometheus 文本格式。"""
    lines: list[str] = []
    for k in sorted(snapshot()):
        v = _counters[k]
        t = "counter" if k.endswith(("_total", "_count")) else "gauge"
        lines.append(f"# TYPE {k} {t}")
        lines.append(f"{k} {v:.6f}")
    lines.append("# TYPE app_uptime_seconds gauge")
    lines.append(f"app_uptime_seconds {uptime_seconds():.3f}")
    return "\n".join(lines) + "\n"


# ---------- 结构化日志 ----------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO, json_format: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    if json_format:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
