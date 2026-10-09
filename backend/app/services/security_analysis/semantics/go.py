"""Go 调用实参与形参的基础绑定语义。"""

from .base import PositionalLanguageSemantics


class GoLanguageSemantics(PositionalLanguageSemantics):
    """按 Go 声明顺序绑定位置参数，不推断接口动态派发或多返回值。"""

    language = "go"
