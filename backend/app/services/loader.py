# -*- coding: utf-8 -*-
import os
import shutil
import uuid
import git
import zipfile


def load_git_repo(repo_url: str, user_id: str) -> tuple[str, str]:
    project_id = str(uuid.uuid4())[:8]
    target_dir = os.path.abspath(f"backend/storage/users/{user_id}/projects/{project_id}")
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    try:
        git.Repo.clone_from(repo_url, target_dir, depth=1)
    except Exception as e:
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
    return project_id, target_dir


def load_zip_file(file_obj, user_id: str) -> tuple[str, str]:
    project_id = str(uuid.uuid4())[:8]
    target_dir = os.path.abspath(f"backend/storage/users/{user_id}/projects/{project_id}")
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    temp_zip_path = os.path.join(target_dir, "uploaded.zip")
    with open(temp_zip_path, "wb") as buffer:
        shutil.copyfileobj(file_obj, buffer)

    with zipfile.ZipFile(temp_zip_path, "r") as zip_ref:
        for zip_info in zip_ref.infolist():
            filename = zip_info.filename
            if filename.startswith("/") or ".." in filename:
                continue
            zip_ref.extract(zip_info, target_dir)

    if os.path.exists(temp_zip_path):
        os.remove(temp_zip_path)
    return project_id, target_dir