from __future__ import annotations

from pathlib import Path

MAX_SOURCE_BYTES = 1024 * 1024
MAX_READ_LINES = 200
IGNORED_DIRECTORIES = {
    ".goenv",
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".idea", ".vscode", ".pytest_cache",
}
TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp", ".json", ".toml", ".yaml", ".yml", ".xml",
    ".md", ".txt", ".ini", ".cfg", ".conf", ".sh", ".ps1", ".bat", ".sql",
    ".html", ".css", ".scss", ".vue", ".svelte", ".gradle", ".properties",
}

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
