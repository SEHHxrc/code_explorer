"""静态分析模块共享的源码语言发现目录。"""

SOURCE_LANGUAGE_BY_EXTENSION = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".h++": "cpp",
}
"""项目源码扩展名到规范语言标识的唯一目录。"""

IGNORED_SOURCE_DIRECTORIES = frozenset({
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    "venv", ".venv", "env", "dist", "build", "out", "target", "vendor", "third_party",
    ".idea", ".vscode", ".next", ".nuxt", "coverage", "bin", "obj", "Pods", ".tox",
})
"""依赖图与程序图回退扫描共同忽略的目录。"""

C_HEADER_EXTENSIONS = frozenset({".h", ".hpp", ".hh", ".hxx", ".h++"})
"""需要结合内容或同名源文件判断 C/C++ 的头文件扩展名。"""

CPP_HEADER_MARKERS = (
    b"namespace ", b"class ", b"template<", b"template <", b"public:", b"private:",
    b"protected:", b"::", b"std::", b"virtual ", b"operator", b"nullptr",
)
"""没有同名源文件时用于识别 C++ 头文件的保守内容标记。"""


def extensions_for_language(language: str) -> frozenset[str]:
    """返回规范语言标识对应的全部源码扩展名。"""
    return frozenset(
        extension
        for extension, registered in SOURCE_LANGUAGE_BY_EXTENSION.items()
        if registered == language
    )
