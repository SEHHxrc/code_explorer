"""项目分析应用服务公开的稳定异常类型。"""


class ProjectAnalysisError(Exception):
    """携带安全公开消息、HTTP 状态和失败阶段的应用异常。"""

    def __init__(self, public_message: str, *, stage: str, status_code: int) -> None:
        """保存可公开消息、失败阶段和 HTTP 状态码。"""
        super().__init__(public_message)
        self.public_message = public_message
        self.stage = stage
        self.status_code = status_code


class InvalidProjectSourceError(ProjectAnalysisError):
    """请求没有提供唯一、可用的项目来源。"""

    def __init__(self, message: str = "Provide exactly one project source: repo_url or ZIP file.") -> None:
        """把无效项目来源映射为 source 阶段的 400 错误。"""
        super().__init__(message, stage="source", status_code=400)


class ProjectImportError(ProjectAnalysisError):
    """远程仓库或 ZIP 无法安全导入。"""

    def __init__(self, message: str = "Unable to import the requested project.", *, status_code: int = 422) -> None:
        """把项目获取失败映射为 import 阶段错误。"""
        super().__init__(message, stage="import", status_code=status_code)


class DependencyAnalysisError(ProjectAnalysisError):
    """确定性代码分析失败；禁止伪造为空图的成功结果。"""

    def __init__(self, message: str = "Project dependency analysis failed.") -> None:
        """把依赖分析失败映射为 analysis 阶段的 422 错误。"""
        super().__init__(message, stage="analysis", status_code=422)


class ProjectPersistenceError(ProjectAnalysisError):
    """分析已完成但项目元数据或分析产物无法可靠保存。"""

    def __init__(self, message: str = "Unable to persist project analysis.") -> None:
        """把项目元数据保存失败映射为 persistence 阶段错误。"""
        super().__init__(message, stage="persistence", status_code=500)

class ArtifactPersistenceError(ProjectAnalysisError):
    """分析产物无法完成原子持久化。"""

    def __init__(self, message: str = "Unable to persist the project analysis artifact.") -> None:
        """把分析产物保存失败映射为 artifact 阶段错误。"""
        super().__init__(message, stage="artifact", status_code=500)