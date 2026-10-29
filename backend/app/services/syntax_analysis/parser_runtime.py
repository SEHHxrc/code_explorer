"""解析器运行资源：安装阶段准备缓存，项目分析阶段只允许使用本地语法。"""

from __future__ import annotations

import os
import platform
import shutil
import threading
from importlib.metadata import version
from pathlib import Path

from tree_sitter_language_pack import PackConfig, cache_dir, configure, downloaded_languages, prefetch

SUPPORTED_GRAMMARS = frozenset({"python", "java", "javascript", "typescript", "tsx", "go", "c", "cpp", "rust"})
_CONFIGURATION_LOCK = threading.Lock()
_configured = False


class ParserUnavailableError(RuntimeError):
    """解析器资源缺失或不可加载；不等同于被分析源码的语法错误。"""

    def __init__(self, languages: set[str]) -> None:
        """输入失败语法名称集合，输出不含内部路径的安装指引。"""
        names = ", ".join(sorted(languages))
        self.public_message = (
            f"分析环境缺少或无法加载本地语法解析器：{names}。"
            "请先运行 README 中的解析器准备命令，然后重启后端；本次导入已停止，不会保存不完整分析。"
        )
        super().__init__(self.public_message)


def parser_cache_directory() -> Path:
    """返回服务端缓存根；默认按安装版本和平台隔离，不依赖用户配置文件目录。"""
    configured = os.environ.get("CODE_EXPLORER_PARSER_CACHE_DIR", "").strip().strip('"')
    if configured:
        return Path(configured).resolve()
    backend_root = Path(__file__).resolve().parents[3]
    return backend_root / "storage" / "runtime" / "tree-sitter" / version("tree-sitter-language-pack") / (
        platform.system().lower() + "-" + platform.machine().lower()
    )


def configure_parser_runtime() -> None:
    """一次性选择固定缓存目录；只配置库，不联网、不下载、不创建解析器。"""
    global _configured
    with _CONFIGURATION_LOCK:
        if not _configured:
            configure(PackConfig(cache_dir=str(parser_cache_directory())))
            _configured = True


def require_local_grammars(languages: set[str]) -> None:
    """仅检查本地库文件；缺少语法时快速失败，禁止进入隐式下载流程。"""
    configure_parser_runtime()
    missing = languages - set(downloaded_languages())
    if missing:
        raise ParserUnavailableError(missing)


def prepare_parsers() -> list[str]:
    """显式安装入口：复用同版本本机缓存，再预取缺少语法，返回已安装名称。

    仅由管理员在安装/迁移时调用，不能从项目上传或 Agent 工具调用。允许联网，
    但不读取或发送项目源码。缓存覆盖需使用与当前安装版本、系统匹配的库文件。
    """
    previous_cache = Path(cache_dir())
    target = parser_cache_directory()
    target.mkdir(parents=True, exist_ok=True)
    if previous_cache.resolve() != target.resolve():
        for language in SUPPORTED_GRAMMARS:
            for source in previous_cache.glob(f"tree_sitter_{language}.*"):
                destination = target / source.name
                if source.is_file() and not destination.exists():
                    shutil.copy2(source, destination)
    configure_parser_runtime()
    prefetch(sorted(SUPPORTED_GRAMMARS))
    return sorted(set(downloaded_languages()) & SUPPORTED_GRAMMARS)
