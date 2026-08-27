"""带实际写入预算和路径校验的 ZIP 项目来源。"""

from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

from ..exceptions import AcquisitionError, SourceValidationError, WorkspacePolicyError
from ..policy import WorkspacePolicy


class ZipProjectSource:
    """先保存部分上传文件，再将安全条目解压到暂存工作区。"""

    def __init__(self, policy: WorkspacePolicy):
        self.policy = policy

    def acquire(self, file_obj, filename: str | None, operation_root: Path, destination: Path) -> str:
        archive = operation_root / "upload.part"
        try:
            self._write_upload(file_obj, archive)
            if not zipfile.is_zipfile(archive):
                raise SourceValidationError("Uploaded file is not a valid ZIP archive.")
            self._extract(archive, destination)
            archive.unlink(missing_ok=True)
        except (SourceValidationError, WorkspacePolicyError):
            raise
        except Exception as exc:
            raise AcquisitionError("Unable to read or extract the uploaded ZIP project.") from exc
        safe_name = os.path.basename(filename or "project.zip").replace("\x00", "")
        return f"local_upload://{safe_name or 'project.zip'}"

    def _write_upload(self, file_obj, archive: Path) -> None:
        written = 0
        with archive.open("wb") as output:
            while chunk := file_obj.read(1024 * 1024):
                written += len(chunk)
                if written > self.policy.max_archive_bytes:
                    raise WorkspacePolicyError("Uploaded ZIP exceeds the archive size limit.")
                output.write(chunk)

    def _extract(self, archive: Path, destination: Path) -> None:
        actual_total = 0
        with zipfile.ZipFile(archive, "r") as bundle:
            entries = bundle.infolist()
            if len(entries) > self.policy.max_archive_files:
                raise WorkspacePolicyError("Uploaded ZIP contains too many files.")
            for entry in entries:
                relative = self._validate_entry(entry)
                target = destination.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise WorkspacePolicyError("Uploaded ZIP contains an unsafe path.")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry, "r") as source, target.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        actual_total += len(chunk)
                        if actual_total > self.policy.max_extracted_bytes:
                            raise WorkspacePolicyError("Uploaded ZIP expands beyond the workspace size limit.")
                        output.write(chunk)

    def _validate_entry(self, entry: zipfile.ZipInfo) -> PurePosixPath:
        normalized = entry.filename.replace("\\", "/")
        relative = PurePosixPath(normalized)
        unix_mode = entry.external_attr >> 16
        ratio = entry.file_size / max(entry.compress_size, 1)
        if (
            not normalized
            or "\x00" in normalized
            or relative.is_absolute()
            or ".." in relative.parts
            or any(":" in part for part in relative.parts)
            or stat.S_ISLNK(unix_mode)
        ):
            raise WorkspacePolicyError("Uploaded ZIP contains an unsafe entry.")
        if ratio > self.policy.max_compression_ratio:
            raise WorkspacePolicyError("Uploaded ZIP contains a suspicious compression ratio.")
        return relative

