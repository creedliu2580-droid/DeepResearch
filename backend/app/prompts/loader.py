"""
PromptLoader — 从 markdown 文件加载 prompt 模板，支持变量填充。
集中管理，换 prompt 不需要改业务代码。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROMPTS_DIR = Path(__file__).parent


class _SafeDict(dict):
    """缺失变量原样保留，避免 prompt 变更时抛 KeyError。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class PromptLoader:
    """轻量加载器 — 读一次 .md 文件，做变量替换。"""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._dir = prompts_dir or _PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def get(self, name: str, **kwargs: object) -> str:
        """获取并填充 prompt 模板。

        Args:
            name: 文件名（不含 .md 后缀）
            **kwargs: 模板变量键值对

        Returns:
            填充后的完整 prompt 字符串
        """
        if name not in self._cache:
            path = self._dir / f"{name}.md"
            if not path.exists():
                raise FileNotFoundError(f"Prompt file not found: {path}")
            self._cache[name] = path.read_text(encoding="utf-8")

        return self._cache[name].format_map(_SafeDict(kwargs))


@lru_cache(maxsize=1)
def get_prompt_loader() -> PromptLoader:
    return PromptLoader()
