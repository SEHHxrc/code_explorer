"""C/C++ 具名位置参数绑定；指针别名和引用写回由未来副作用模型负责。"""

from .base import PositionalLanguageSemantics


class CLanguageSemantics(PositionalLanguageSemantics):
    """将 C 调用的具名固定前缀参数连接到目标形参。"""

    language = "c"


class CppLanguageSemantics(PositionalLanguageSemantics):
    """复用 C++ 普通位置参数绑定，不补造省略的默认参数或隐式 this。"""

    language = "cpp"
