# -*- coding: utf-8 -*-
# 统一的 API 路由层 backend/app/api/project.py
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from backend.app.core.deps import get_current_user
from backend.app.middleware.sanitizer import ProjectSanitizer
from backend.app.models import SessionLocal
from backend.app.services.analyzer import build_file_tree
from backend.app.services.git_loader import load_git_repo
from backend.app.services.zip_loader import load_zip_file
from backend.app.services.project_adder import add_project_to_db
from backend.app.services.project_cleaner import remove_project_by_id


router = APIRouter(prefix="/api/projects", tags=["Projects"])


@router.post("/analyze")
async def analyze_project(
        repo_url: str | None = Form(default=None),
        file: UploadFile | None = File(default=None),
        current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]

    if repo_url:
        project_id, target_dir = load_git_repo(repo_url, user_id)
        source_tag = repo_url
    elif file:
        # 接收前端传过来的文件流
        project_id, target_dir = load_zip_file(file.file, user_id)
        source_tag = f"local_upload://{file.filename}"
    else:
        return {
            "code": 4001,
            "message": "Either repo_url or file must be provided.",
        }

    # 安全清洗
    sanitize_info = ProjectSanitizer.clean_directory(target_dir)

    # 生成文件树
    file_tree = build_file_tree(target_dir)

    # 入库
    db = SessionLocal()
    add_project_to_db(db, project_id, user_id, source_tag, target_dir, file_tree)

    return {
        "code": 200,
        "message": "Project processed successfully.",
        "data": {
            "project_id": project_id,
            "sanitize_report": sanitize_info,
            "file_tree": file_tree,
        },
    }

@router.delete("/clear/{project_id}")
async def clear_project(project_id: str, current_user: dict = Depends(get_current_user)):
    """删除指定项目"""
    user_id = current_user["user_id"]

    db = SessionLocal()
    try:
        success = remove_project_by_id(db, project_id, user_id)
        if not success:
            raise HTTPException(
                status_code=404, detail="Project not found or unauthorized."
            )

        return {
            "code": 200,
            "message": f"Project {project_id} cleaned up successfully.",
            "data": {"project_id": project_id},
        }
    finally:
        db.close()