"""项目功能域可安全映射到协议层的异常。"""


class ProjectError(Exception):
    """携带公开消息、HTTP 状态和可选失败阶段的项目异常。"""

    def __init__(
        self,
        public_message: str,
        *,
        status_code: int,
        stage: str = "project",
    ) -> None:
        """保存可公开消息、HTTP 状态和失败阶段。"""
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code
        self.stage = stage


class ProjectAnalysisError(ProjectError):
    """项目导入或分析用例失败。"""


class InvalidProjectSourceError(ProjectAnalysisError):
    """请求没有提供唯一且可用的项目来源。"""

    def __init__(self, message: str = "Provide exactly one project source: repo_url or ZIP file.") -> None:
        """把无效来源映射为 source 阶段的 400 错误。"""
        super().__init__(message, stage="source", status_code=400)


class ProjectImportError(ProjectAnalysisError):
    """远程仓库或 ZIP 无法安全导入。"""

    def __init__(
        self,
        message: str = "Unable to import the requested project.",
        *,
        status_code: int = 422,
    ) -> None:
        """把项目获取失败映射为 import 阶段错误。"""
        super().__init__(message, stage="import", status_code=status_code)


class DependencyAnalysisError(ProjectAnalysisError):
    """确定性代码分析失败。"""

    def __init__(self, message: str = "Project dependency analysis failed.") -> None:
        """把依赖分析失败映射为 analysis 阶段的 422 错误。"""
        super().__init__(message, stage="analysis", status_code=422)


class ProjectPersistenceError(ProjectAnalysisError):
    """项目元数据无法可靠保存。"""

    def __init__(self, message: str = "Unable to persist project analysis.") -> None:
        """把项目元数据保存失败映射为 persistence 阶段错误。"""
        super().__init__(message, stage="persistence", status_code=500)


class ArtifactPersistenceError(ProjectAnalysisError):
    """项目分析产物无法完成原子持久化。"""

    def __init__(self, message: str = "Unable to persist the project analysis artifact.") -> None:
        """把产物保存失败映射为 artifact 阶段错误。"""
        super().__init__(message, stage="artifact", status_code=500)


class ProjectQueryError(ProjectError):
    """项目库存或快照查询失败。"""


class ProjectDeletionError(ProjectError):
    """项目删除或删除补偿失败。"""

    def __init__(self, message: str, status_code: int) -> None:
        """保存删除错误的公开消息和 HTTP 状态。"""
        super().__init__(message, stage="deletion", status_code=status_code)
