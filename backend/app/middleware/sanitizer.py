# -*- coding: utf-8 -*-
import os
from enum import Enum


class ForbiddenExtension(Enum):
    EXE = ".exe"
    DLL = ".dll"
    SO = ".so"
    DMG = ".dmg"
    ISO = ".iso"
    BIN = ".bin"
    ZIP = ".zip"
    TAR = ".tar"
    GZ = ".gz"


class SensitiveFilename(Enum):
    ENV = ".env"
    ID_RSA = "id_rsa"
    ID_DSA = "id_dsa"
    CREDENTIALS = "credentials.json"
    CONFIG = "config.json"


class ProjectSanitizer:
    """安全与输入清洗管道"""

    # 编译类常量集合，供快速查找
    FORBIDDEN_EXTS_SET = {e.value for e in ForbiddenExtension}
    SENSITIVE_NAMES_SET = {s.value for s in SensitiveFilename}
    IGNORED_DIRS_SET = {
        ".git",
        "node_modules",
        "__pycache__",
        "venv",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 单文件最大 5MB 限制

    @classmethod
    def clean_directory(cls, target_dir: str) -> dict:
        removed_count = 0
        scanned_count = 0

        for root, dirs, files in os.walk(target_dir):
            # 过滤噪音目录
            dirs[:] = [d for d in dirs if d not in cls.IGNORED_DIRS_SET]

            for file in files:
                scanned_count += 1
                file_path = os.path.join(root, file)
                _, ext = os.path.splitext(file)

                # 1. 拦截敏感文件或黑名单后缀
                if file in cls.SENSITIVE_NAMES_SET or ext.lower() in cls.FORBIDDEN_EXTS_SET:
                    try:
                        os.remove(file_path)
                        removed_count += 1
                        continue
                    except Exception:
                        pass

                # 2. 拦截超大文件，防止内存溢出
                try:
                    if os.path.getsize(file_path) > cls.MAX_FILE_SIZE:
                        os.remove(file_path)
                        removed_count += 1
                except Exception:
                    pass

        return {
            "scanned_files": scanned_count,
            "filtered_out_files": removed_count,
        }