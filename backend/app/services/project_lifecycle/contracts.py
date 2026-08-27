"""项目生命周期用例的稳定输出契约。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectDeletionResult:
    """项目删除完成后的资源结果。"""

    project_id: str
    deleted: bool = True
    warnings: list[str] = field(default_factory=list)


class ProjectLifecycleError(Exception):
    """可安全映射到 HTTP 的项目生命周期错误。"""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.public_message = message
        self.status_code = status_code