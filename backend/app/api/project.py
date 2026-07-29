# -*- coding: utf-8 -*-
# 统一的 API 路由层 backend/app/api/project.py
from fastapi import APIRouter, Depends, File, Form, UploadFile
from backend.app.core.deps import get_current_user
from backend.app.middleware.sanitizer import ProjectSanitizer
from backend.app.models import ProjectModel, SessionLocal
from backend.app.services.analyzer import build_file_tree
from backend.app.services.git_loader import load_git_repo
from backend.app.services.zip_loader import load_zip_file

router = APIRouter(prefix="/api/projects", tags=["Projects"])


@router.post("/analyze")
async def analyze_project(
    repo_url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    current_user: dict = Depends(get_current_user),
):
  user_id = current_user["user_id"]

  # 1. 载入源码 (Git 或 本地 Zip)
  if repo_url:
    project_id, target_dir = load_git_repo(repo_url, user_id)
    source_tag = repo_url
  elif file:
    project_id, target_dir = load_zip_file(file.file, user_id)
    source_tag = f"local_upload://{file.filename}"
  else:
    return {
        "code": 4001,
        "message": "Either repo_url or file must be provided.",
    }

  # 2. 中间层：安全清洗与过滤
  sanitize_info = ProjectSanitizer.clean_directory(target_dir)

  # 3. 静态分析：生成前端所需的文件树
  file_tree = build_file_tree(target_dir)

  # 4. 存入数据库（携带 user_id）
  db = SessionLocal()
  db.add(
      ProjectModel(
          id=project_id,
          user_id=user_id,
          repo_url=source_tag,
          local_path=target_dir,
          file_tree=file_tree,
      )
  )
  db.commit()
  db.close()

  return {
      "code": 200,
      "message": "Project processed successfully.",
      "data": {
          "project_id": project_id,
          "sanitize_report": sanitize_info,
          "file_tree": file_tree,
      },
  }