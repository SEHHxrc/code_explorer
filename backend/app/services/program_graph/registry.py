"""公共程序图语言前端注册表。"""

from __future__ import annotations

from .frontends import ProgramGraphFrontend


class ProgramGraphFrontendRegistry:
    """按语言唯一注册控制 IR 前端。"""

    def __init__(self, frontends: list[ProgramGraphFrontend] | None = None) -> None:
        """注册初始前端并拒绝语言重复。"""
        self._frontends: dict[str, ProgramGraphFrontend] = {}
        for frontend in frontends or []:
            self.register(frontend)

    def register(self, frontend: ProgramGraphFrontend) -> None:
        """注册一个语言前端。"""
        if frontend.language in self._frontends:
            raise ValueError(f"Duplicate program-graph frontend: {frontend.language}")
        self._frontends[frontend.language] = frontend

    def get(self, language: str) -> ProgramGraphFrontend | None:
        """返回对应语言前端；未支持时返回 ``None``。"""
        return self._frontends.get(language)

    def languages(self) -> tuple[str, ...]:
        """返回稳定排序的已支持语言。"""
        return tuple(sorted(self._frontends))
