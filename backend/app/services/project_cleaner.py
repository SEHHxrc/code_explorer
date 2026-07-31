import os
import shutil
import stat
from sqlalchemy.orm import Session
from backend.app.models import ProjectModel


def _remove_readonly(func, path):
    """错误处理器：当 shutil.rmtree 因为 Windows 只读权限报错时，

    强行修改文件权限为可写 (W_OK)，然后再次尝试删除。
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"[Warning] Failed to remove readonly for {path}: {e}")

def remove_project_by_id(db: Session, project_id: str, user_id: str) -> bool:
    """清理服务：负责从数据库和本地物理沙箱中彻底移除指定项目"""

    # 1. 查找属于该用户的项目记录
    project = (db.query(ProjectModel).filter(ProjectModel.id == project_id, ProjectModel.user_id == user_id).first())

    if not project:
        return False

    # 2. 从数据库中移除记录
    try:
        db.delete(project)
        db.commit()
        db.close()
    except Exception as e:
        print(f"[Warning] Failed to delete project {project_id}: {e}")
        db.rollback()
        db.close()
        return False

    # 3. 删除服务器本地物理存储目录
    target_dir = project.local_path
    if target_dir and os.path.exists(target_dir):
        try:
            # 使用 onerror 传入错误处理器，解决 Windows 下 .git 文件只读拒绝访问的问题
            shutil.rmtree(target_dir, onerror=_remove_readonly)
        except Exception as e:
            print(f"[Warning] Failed to delete physical dir {target_dir}: {e}")
            return False

    return True