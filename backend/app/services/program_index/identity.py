"""为文件、符号、位置和调用点生成跨分析阶段稳定的身份。"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath


class ProgramIdentity:
    """集中定义依赖图、安全 IR 和未来数据流共同使用的身份算法。"""

    @staticmethod
    def file_id(path: str) -> str:
        """把项目相对路径规范化为正斜杠文件身份。"""
        normalized = str(path or "").replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return PurePosixPath(normalized).as_posix() if normalized else ""

    @classmethod
    def symbol_id(cls, file_path: str, scope_names: list[str] | tuple[str, ...]) -> str:
        """使用规范文件身份和嵌套作用域名称生成符号 FQN。"""
        file_identity = cls.file_id(file_path)
        return file_identity + ("::" + "::".join(scope_names) if scope_names else "")

    @classmethod
    def location_id(
        cls,
        path: str,
        line: int,
        column: int,
        end_line: int,
        end_column: int,
    ) -> str:
        """根据规范化源码范围生成稳定位置身份。"""
        material = "\x1f".join((
            cls.file_id(path),
            str(max(0, int(line or 0))),
            str(max(0, int(column or 0))),
            str(max(0, int(end_line or 0))),
            str(max(0, int(end_column or 0))),
        ))
        return "location:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def callsite_id(
        cls,
        path: str,
        line: int,
        column: int,
        end_line: int,
        end_column: int,
    ) -> str:
        """生成与解析目标无关的调用表达式身份。"""
        location = cls.location_id(path, line, column, end_line, end_column)
        return "callsite:" + location.split(":", 1)[1]
