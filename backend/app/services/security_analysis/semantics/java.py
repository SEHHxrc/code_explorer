"""Java 调用实参与形参的基础绑定语义。"""

from .base import PositionalLanguageSemantics


class JavaLanguageSemantics(PositionalLanguageSemantics):
    """按 Java 位置参数建立保守绑定，不自行推断动态派发目标。"""

    language = "java"
