"""受控工作区的输入、阶段产物与清洗报告。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Literal


@dataclass(frozen=True)
class WorkspaceSource:
    """Git URL 或 ZIP 文件流二选一的工作区来源。"""

    kind: Literal["git", "zip"]
    repo_url: str | None = None
    file_obj: BinaryIO | None = None
    filename: str | None = None

    @classmethod
    def git(cls, repo_url: str) -> "WorkspaceSource":
        return cls(kind="git", repo_url=repo_url)

    @classmethod
    def zip(cls, file_obj: BinaryIO, filename: str | None) -> "WorkspaceSource":
        return cls(kind="zip", file_obj=file_obj, filename=filename)


@dataclass(frozen=True)
class WorkspaceOperation:
    """一次导入操作的可信标识和由路径服务生成的位置。"""

    operation_id: str
    project_id: str
    user_id: str
    operation_root: Path
    source_root: Path
    final_root: Path


@dataclass(frozen=True)
class SanitizeReport:
    """不暴露具体敏感路径的分类清洗计数。"""

    scanned_files: int = 0
    filtered_out_files: int = 0
    removed_sensitive_files: int = 0
    removed_forbidden_files: int = 0
    removed_oversized_files: int = 0
    removed_links: int = 0
    removed_noise_directories: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class PreparedWorkspace:
    """已获取并完成安全清洗、但尚未发布的项目工作区。"""

    operation: WorkspaceOperation
    source_tag: str
    sanitize_report: SanitizeReport

