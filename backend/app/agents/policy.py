# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path


MAX_SOURCE_BYTES = 1024 * 1024
MAX_READ_LINES = 200
IGNORED_DIRECTORIES = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".idea", ".vscode", ".pytest_cache",
}
TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp", ".json", ".toml", ".yaml", ".yml", ".xml",
    ".md", ".txt", ".ini", ".cfg", ".conf", ".sh", ".ps1", ".bat", ".sql",
    ".html", ".css", ".scss", ".vue", ".svelte", ".gradle", ".properties",
}

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*(['\"]?)[^\s,'\"]+\2"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def resolve_project_path(project_root: str | Path, relative_path: str) -> Path:
    """安全解析项目内路径。

    输入项目根目录和相对路径，输出规范化绝对路径；若结果越出项目根目录则抛出
    ``ValueError``。
    """
    root = Path(project_root).resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes the project workspace") from exc
    return candidate


def redact_secrets(text: str) -> str:
    """遮盖上下文中的常见令牌、密码和私钥标记，输出脱敏文本。"""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
