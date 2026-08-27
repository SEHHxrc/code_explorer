# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ARTIFACT_ROOT = Path("backend/storage/artifacts").resolve()


def _artifact_path(project_id: str, suffix: str = ".json") -> Path:
    """校验项目 ID 并输出位于固定产物目录下的文件路径。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
        raise ValueError("Invalid project id")
    return ARTIFACT_ROOT / f"{project_id}{suffix}"


def save_analysis_artifact(project_id: str, payload: dict[str, Any]) -> None:
    """输入项目 ID 和分析字典，通过临时文件替换原子保存 JSON；无返回值。"""
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    target = _artifact_path(project_id)
    temporary = _artifact_path(project_id, ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def load_analysis_artifact(project_id: str) -> dict[str, Any] | None:
    """输入项目 ID，输出已解析分析字典；产物不存在时输出 ``None``。"""
    target = _artifact_path(project_id)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def remove_analysis_artifact(project_id: str) -> None:
    """输入项目 ID，幂等删除正式产物和可能残留的原子写入临时文件。"""
    for target in (_artifact_path(project_id), _artifact_path(project_id, ".tmp")):
        if target.exists():
            target.unlink()
