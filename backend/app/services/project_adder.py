from sqlalchemy.orm import Session
from backend.app.models import ProjectModel


def add_project_to_db(db: Session, project_id: str, user_id: str, source_tag: str, target_dir: str, file_tree: list)->bool:
    try:
        db.add(
            ProjectModel(id=project_id, user_id=user_id, repo_url=source_tag, local_path=target_dir, file_tree=file_tree, ))
        db.commit()
        db.close()
        return True
    except Exception as e:
        print(f"[Warning] Failed to add project {project_id}: {e}")
        db.rollback()
        db.close()
        return False