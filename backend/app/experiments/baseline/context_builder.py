"""TEMPORARY CONTROL GROUP / 临时原始文件对照组；实验结束后可整体删除。"""

from backend.app.experiments.context import SecurityExperimentContextBuilder


class BaselineContextBuilder(SecurityExperimentContextBuilder):
    """【临时对照组】不接收静态安全证据，只使用与实验组相同的原始文件工具。"""

    def __init__(self) -> None:
        """禁用唯一实验变量——静态安全证据包输入。"""
        super().__init__(with_evidence=False)
