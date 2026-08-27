"""受控项目工作区的容量和安全策略。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkspacePolicy:
    """集中定义项目获取、解压、清洗和存储预算。"""

    max_archive_bytes: int = 200 * 1024 * 1024
    max_extracted_bytes: int = 512 * 1024 * 1024
    max_archive_files: int = 20_000
    max_file_bytes: int = 5 * 1024 * 1024
    max_compression_ratio: int = 200
    git_timeout_seconds: int = 120
    stale_operation_seconds: int = 6 * 60 * 60
    allowed_git_hosts: frozenset[str] = field(default_factory=frozenset)

