"""工作区服务的稳定失败类型。"""


class WorkspaceError(Exception):
    """包含阶段、公开消息和建议 HTTP 状态的工作区异常。"""

    def __init__(self, message: str, *, stage: str, status_code: int = 422) -> None:
        """保存公开消息、失败阶段和建议 HTTP 状态码。"""
        super().__init__(message)
        self.public_message = message
        self.stage = stage
        self.status_code = status_code


class SourceValidationError(WorkspaceError):
    """表示项目来源未通过输入与网络安全校验。"""
    def __init__(self, message: str) -> None:
        """把来源校验失败固定为 source 阶段的 400 错误。"""
        super().__init__(message, stage="source", status_code=400)


class AcquisitionError(WorkspaceError):
    """表示项目源码获取过程失败。"""
    def __init__(self, message: str = "Unable to acquire the requested project source.") -> None:
        """把源码获取失败固定为 acquiring 阶段错误。"""
        super().__init__(message, stage="acquiring", status_code=422)


class WorkspacePolicyError(WorkspaceError):
    """表示工作区内容违反清洗或资源限制策略。"""
    def __init__(self, message: str) -> None:
        """把安全策略拒绝固定为 sanitizing 阶段错误。"""
        super().__init__(message, stage="sanitizing", status_code=422)


class WorkspacePublishError(WorkspaceError):
    """表示暂存工作区无法原子发布。"""
    def __init__(self, message: str = "Unable to publish the analyzed project workspace.") -> None:
        """把原子发布失败固定为 publishing 阶段的 500 错误。"""
        super().__init__(message, stage="publishing", status_code=500)

