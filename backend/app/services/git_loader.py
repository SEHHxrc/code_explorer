# -*- coding: utf-8 -*-
import os
import shutil
import uuid
import git


def load_git_repo(repo_url: str, user_id: str) -> tuple[str, str]:
    project_id = str(uuid.uuid4())[:8]
    target_dir = os.path.abspath(f"backend/storage/users/{user_id}/projects/{project_id}")
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    git.Repo.clone_from(repo_url, target_dir, depth=1)
    return project_id, target_dir