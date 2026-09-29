"""依赖图与程序图共享的源码语法基础设施。"""

from .catalog import (
    C_HEADER_EXTENSIONS,
    CPP_HEADER_MARKERS,
    IGNORED_SOURCE_DIRECTORIES,
    SOURCE_LANGUAGE_BY_EXTENSION,
    extensions_for_language,
)
from .tree_parser import (
    DEFAULT_TREE_SITTER_PARSER_POOL,
    TreeSitterParserPool,
    descend_for,
    field,
    fields,
    first_of,
    normalize_type,
    split_qualified,
    text,
)

__all__ = [
    "CPP_HEADER_MARKERS",
    "C_HEADER_EXTENSIONS",
    "DEFAULT_TREE_SITTER_PARSER_POOL",
    "IGNORED_SOURCE_DIRECTORIES",
    "SOURCE_LANGUAGE_BY_EXTENSION",
    "TreeSitterParserPool",
    "descend_for",
    "extensions_for_language",
    "field",
    "fields",
    "first_of",
    "normalize_type",
    "split_qualified",
    "text",
]
