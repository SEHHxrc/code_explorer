"""项目分析产物的原子文件存储与仓储接口。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ARTIFACT_ROOT = Path("backend/storage/artifacts").resolve()
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _identifier(value: str, label: str) -> str:
    """校验项目或操作标识可安全参与路径计算。"""
    if not _IDENTIFIER.fullmatch(value or ""):
        raise ValueError(f"Invalid {label}")
    return value


def _artifact_path(project_id: str, suffix: str = ".json") -> Path:
    """返回固定产物根目录中的项目文件路径。"""
    return ARTIFACT_ROOT / f"{_identifier(project_id, 'project id')}{suffix}"


def _deletion_root(operation_id: str) -> Path:
    """返回一次删除操作隔离产物的受控目录。"""
    return ARTIFACT_ROOT / ".deleting" / _identifier(operation_id, "operation id")


def save_analysis_artifact(project_id: str, payload: dict[str, Any]) -> None:
    """通过同目录临时文件替换，原子创建或更新项目分析产物。"""
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    target = _artifact_path(project_id)
    temporary = _artifact_path(project_id, ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def load_analysis_artifact(project_id: str) -> dict[str, Any] | None:
    """读取项目分析产物；文件不存在时返回 ``None``。"""
    target = _artifact_path(project_id)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def analysis_artifact_size(project_id: str) -> int | None:
    """返回正式分析产物字节数；文件不可访问时返回 ``None``。"""
    target = _artifact_path(project_id)
    try:
        return target.stat().st_size if target.is_file() else None
    except OSError:
        return None


def remove_analysis_artifact(project_id: str) -> None:
    """幂等删除正式产物和可能残留的原子写入临时文件。"""
    for target in (_artifact_path(project_id), _artifact_path(project_id, ".tmp")):
        if target.exists():
            target.unlink()


class ProjectArtifactRepository:
    """封装项目分析产物的创建、读取、更新、统计和删除操作。"""

    def save(self, project_id: str, payload: dict[str, Any]) -> None:
        """原子创建或替换项目分析产物。"""
        save_analysis_artifact(project_id, payload)

    def load(self, project_id: str) -> dict[str, Any] | None:
        """读取项目分析产物。"""
        return load_analysis_artifact(project_id)

    def size(self, project_id: str) -> int | None:
        """返回项目分析产物字节数。"""
        return analysis_artifact_size(project_id)

    def remove(self, project_id: str) -> None:
        """幂等删除项目分析产物。"""
        remove_analysis_artifact(project_id)

    def quarantine(self, project_id: str, operation_id: str) -> bool:
        """把项目产物原子移动到删除隔离区，并返回是否移动了文件。"""
        deletion_root = _deletion_root(operation_id)
        moved = False
        for suffix in (".json", ".tmp"):
            source = _artifact_path(project_id, suffix)
            if not source.exists():
                continue
            deletion_root.mkdir(parents=True, exist_ok=True)
            target = deletion_root / source.name
            if target.exists():
                raise FileExistsError("Artifact deletion quarantine already exists")
            source.replace(target)
            moved = True
        return moved

    def restore(self, project_id: str, operation_id: str) -> None:
        """把隔离产物恢复到正式位置；目标已存在时拒绝覆盖。"""
        deletion_root = _deletion_root(operation_id)
        if not deletion_root.is_dir():
            return
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        for suffix in (".json", ".tmp"):
            target = _artifact_path(project_id, suffix)
            source = deletion_root / target.name
            if not source.exists():
                continue
            if target.exists():
                raise FileExistsError("Refusing to overwrite an existing project artifact")
            source.replace(target)
        try:
            deletion_root.rmdir()
            deletion_root.parent.rmdir()
        except OSError:
            pass

    def purge_quarantine(self, operation_id: str) -> None:
        """幂等清除一次已提交删除操作的隔离产物。"""
        deletion_root = _deletion_root(operation_id)
        if not deletion_root.is_dir():
            return
        for target in deletion_root.iterdir():
            if target.is_file() and not target.is_symlink():
                target.unlink()
            elif target.is_symlink():
                target.unlink()
            else:
                raise OSError("Unexpected directory in artifact deletion quarantine")
        deletion_root.rmdir()
        try:
            deletion_root.parent.rmdir()
        except OSError:
            pass


AnalysisArtifactRepository = ProjectArtifactRepository

__all__ = [
    "ARTIFACT_ROOT",
    "AnalysisArtifactRepository",
    "ProjectArtifactRepository",
    "analysis_artifact_size",
    "load_analysis_artifact",
    "remove_analysis_artifact",
    "save_analysis_artifact",
]
