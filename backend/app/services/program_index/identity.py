"""为文件、符号、位置和调用点生成跨分析阶段稳定的身份。"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import PurePosixPath


@lru_cache(maxsize=4096)
def _file_identity(path: str) -> str:
    """有界缓存纯路径规范化，不缓存源码或权限判定；保留原有字符串转换语义。"""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized).as_posix() if normalized else ""


class ProgramIdentity:
    """集中定义依赖图、安全 IR 和未来数据流共同使用的身份算法。"""

    @staticmethod
    def file_id(path: str) -> str:
        """把项目相对路径规范化为正斜杠文件身份。"""
        return _file_identity(str(path or ""))

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
        return cls.callsite_for_location(location)

    @staticmethod
    def callsite_for_location(location_id: str) -> str:
        """复用已生成的位置身份，不重复规范路径和计算同一源码范围的摘要。"""
        prefix, separator, identity = location_id.partition(":")
        if prefix != "location" or not separator or not identity:
            raise ValueError("Callsite requires a generated location identity")
        return "callsite:" + identity
