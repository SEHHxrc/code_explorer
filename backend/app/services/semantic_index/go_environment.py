"""Go 环境配置的旧路径兼容入口；不保留第二套环境准备实现。"""

from ..language_backends.go_environment import GoAnalysisConfig, LOCAL_ENVIRONMENT

__all__ = ["GoAnalysisConfig", "LOCAL_ENVIRONMENT"]
