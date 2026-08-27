"""A/B 实验两组共享的中性仓库上下文构造函数。"""

from __future__ import annotations

from backend.app.schemas.manifest import ProjectManifest
from backend.app.services.code_intelligence.repo_map_builder import build_repo_map


def neutral_manifest(artifact: dict) -> ProjectManifest:
    """移除图统计，输出两组共同使用的 Manifest 基础事实。"""
    manifest = ProjectManifest.model_validate(artifact.get("manifest") or {})
    return manifest.model_copy(update={"graph_summary": {}})


def neutral_repo_map(artifact: dict) -> str:
    """按文件与符号稳定排序构建 Repo Map，不使用图中心性加权。"""
    return build_repo_map(neutral_manifest(artifact), artifact.get("file_symbols") or {})