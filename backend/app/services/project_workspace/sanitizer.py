"""对已复制到受控暂存区的项目执行失败关闭的安全清洗。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .contracts import SanitizeReport
from .exceptions import WorkspacePolicyError
from .filesystem import WorkspaceFilesystem
from .policy import WorkspacePolicy


FORBIDDEN_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dmg", ".iso", ".bin", ".zip", ".tar", ".gz",
}
SENSITIVE_FILENAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials.json",
}
NOISE_DIRECTORIES = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build",
    ".idea", ".vscode",
}
_REPARSE_POINT = 0x400


class ProjectSanitizer:
    """删除不应进入分析和执行环境的内容；无法删除时拒绝项目。"""

    def __init__(self, policy: WorkspacePolicy, filesystem: WorkspaceFilesystem):
        self.policy = policy
        self.filesystem = filesystem

    def clean(self, root: Path) -> SanitizeReport:
        counters = {
            "scanned_files": 0,
            "filtered_out_files": 0,
            "removed_sensitive_files": 0,
            "removed_forbidden_files": 0,
            "removed_oversized_files": 0,
            "removed_links": 0,
            "removed_noise_directories": 0,
        }
        total_bytes = 0
        try:
            for current, directories, files in os.walk(root, topdown=True, followlinks=False):
                current_path = Path(current)
                kept_directories = []
                for name in directories:
                    path = current_path / name
                    if name.casefold() in NOISE_DIRECTORIES:
                        self.filesystem.remove_child(path, root)
                        counters["removed_noise_directories"] += 1
                        continue
                    if self._is_link_or_reparse(path):
                        self.filesystem.remove_child(path, root)
                        counters["removed_links"] += 1
                        counters["filtered_out_files"] += 1
                        continue
                    kept_directories.append(name)
                directories[:] = kept_directories

                for name in files:
                    path = current_path / name
                    counters["scanned_files"] += 1
                    if counters["scanned_files"] > self.policy.max_archive_files:
                        raise WorkspacePolicyError("Project contains too many files.")
                    if self._is_link_or_reparse(path):
                        self.filesystem.remove_child(path, root)
                        counters["removed_links"] += 1
                        counters["filtered_out_files"] += 1
                        continue
                    size = path.stat().st_size
                    total_bytes += size
                    if total_bytes > self.policy.max_extracted_bytes:
                        raise WorkspacePolicyError("Project exceeds the total workspace size limit.")
                    lowered = name.casefold()
                    if lowered == ".env" or lowered.startswith(".env.") or lowered in SENSITIVE_FILENAMES:
                        self.filesystem.remove_child(path, root)
                        counters["removed_sensitive_files"] += 1
                        counters["filtered_out_files"] += 1
                    elif path.suffix.casefold() in FORBIDDEN_EXTENSIONS:
                        self.filesystem.remove_child(path, root)
                        counters["removed_forbidden_files"] += 1
                        counters["filtered_out_files"] += 1
                    elif size > self.policy.max_file_bytes:
                        self.filesystem.remove_child(path, root)
                        counters["removed_oversized_files"] += 1
                        counters["filtered_out_files"] += 1
        except WorkspacePolicyError:
            raise
        except Exception as exc:
            raise WorkspacePolicyError("Unable to safely sanitize the imported project.") from exc
        return SanitizeReport(**counters)

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise WorkspacePolicyError("Unable to inspect an imported filesystem entry.") from exc
        attributes = getattr(metadata, "st_file_attributes", 0)
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT)

