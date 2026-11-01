"""通用工具：健壮的 LLM 输出 JSON 解析。

LLM 返回 JSON 时经常附带 Markdown 代码块、前后缀说明文字，甚至直接返回
非 JSON 文本。这里的函数做多级容错，避免单个节点因解析失败而崩溃。

容错策略（按优先级）：
  1. 剥离 ```json ... ``` / ``` ... ``` 代码块
  2. 直接 json.loads
  3. 用正则提取首对 {...} 或 [...]（容忍前后缀说明）
  4. 字符串数组场景：按行回退，去掉常见列表符号
"""

from __future__ import annotations

import json
import re
from typing import Any


def strip_code_fence(text: str) -> str:
    """剥离 LLM 输出中常见的 Markdown 代码块包裹。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:].strip()
    return t.strip()


def parse_json(text: str) -> Any:
    """解析 LLM 输出的 JSON（对象或数组），失败抛 ValueError。"""
    t = strip_code_fence(text)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # 容忍 LLM 在 JSON 前后附加说明文字：提取首对完整 {...} 或 [...]
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue

    raise ValueError(f"无法从 LLM 输出解析 JSON：{text[:80]!r}")


def parse_json_list(text: str, cap: int | None = None) -> list[str]:
    """解析字符串数组；解析失败按行回退（去掉列表符号与序号）。

    Args:
        text: LLM 原始输出
        cap: 最多返回的条目数；None 表示不截断
    """
    t = (text or "").strip()
    if not t:
        return []

    candidates: list[str] = []
    try:
        data = parse_json(t)
        if isinstance(data, list):
            candidates = [str(x).strip() for x in data if str(x).strip()]
    except ValueError:
        pass

    if not candidates:
        candidates = [
            ln.strip().lstrip("-*•0123456789.) ").strip()
            for ln in t.splitlines()
            if ln.strip()
        ]

    return [c for c in candidates if c][:cap]
