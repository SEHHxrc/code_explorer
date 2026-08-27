"""工作区服务的稳定失败类型。"""


class WorkspaceError(Exception):
    """包含阶段、公开消息和建议 HTTP 状态的工作区异常。"""

    def __init__(self, message: str, *, stage: str, status_code: int = 422):
        super().__init__(message)
        self.public_message = message
        self.stage = stage
        self.status_code = status_code


class SourceValidationError(WorkspaceError):
    def __init__(self, message: str):
        super().__init__(message, stage="source", status_code=400)


class AcquisitionError(WorkspaceError):
    def __init__(self, message: str = "Unable to acquire the requested project source."):
        super().__init__(message, stage="acquiring", status_code=422)


class WorkspacePolicyError(WorkspaceError):
    def __init__(self, message: str):
        super().__init__(message, stage="sanitizing", status_code=422)


class WorkspacePublishError(WorkspaceError):
    def __init__(self, message: str = "Unable to publish the analyzed project workspace."):
        super().__init__(message, stage="publishing", status_code=500)

