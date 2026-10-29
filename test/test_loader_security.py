# -*- coding: utf-8 -*-
import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.app.services.projects.artifacts import ProjectArtifactRepository
from backend.app.services.projects.transaction import ProjectImportTransaction
from backend.app.services.project_workspace import ProjectWorkspaceService, WorkspacePolicy, WorkspaceSource
from backend.app.services.project_workspace.exceptions import SourceValidationError, WorkspacePolicyError
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths
from backend.app.services.project_workspace.sources.git import validate_repo_url
from backend.app.services.project_workspace.service import _default_policy


class NoopArtifacts(ProjectArtifactRepository):
    def remove(self, project_id):
        return None


def zip_bytes(entries):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in entries.items():
            output.writestr(name, content)
    archive.seek(0)
    return archive


class WorkspaceSecurityTests(unittest.TestCase):
    def test_repo_url_rejects_local_non_http_and_private_dns(self):
        policy = WorkspacePolicy(allowed_git_hosts=frozenset())
        with self.assertRaises(SourceValidationError):
            validate_repo_url("file:///etc/passwd", policy)
        with patch("backend.app.services.project_workspace.sources.git.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]), self.assertRaises(SourceValidationError):
            validate_repo_url("https://example.test/repo.git", policy)

    def test_repo_url_honors_configured_host_allowlist(self):
        policy = WorkspacePolicy(allowed_git_hosts=frozenset({"github.com"}))
        with self.assertRaises(SourceValidationError):
            validate_repo_url("https://gitlab.com/example/repo.git", policy)

    def test_default_hosts_include_gitlab_and_preserve_dns_and_exact_host_checks(self):
        """GitLab 子组仓库可用，伪造后缀、凭据和私网 DNS 仍被拒绝。"""
        with patch("backend.app.services.project_workspace.sources.git.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]):
            for url in ("https://gitlab.com/gitlab-org/gitlab-runner.git",
                        "https://gitlab.com/group/subgroup/repository.git",
                        "https://github.com/owner/repository.git"):
                validate_repo_url(url, WorkspacePolicy())
            for url in ("https://gitlab.com.attacker.test/group/repo.git", "https://user:password@gitlab.com/group/repo.git"):
                with self.assertRaises(SourceValidationError):
                    validate_repo_url(url, WorkspacePolicy())
        with patch("backend.app.services.project_workspace.sources.git.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]), self.assertRaisesRegex(SourceValidationError, "Private or local"):
            validate_repo_url("https://gitlab.com/gitlab-org/gitlab-runner.git", WorkspacePolicy())

    def test_host_configuration_defaults_and_explicit_overrides(self):
        """未配置使用两个公共主机；自建实例和显式空配置仍按管理员设置处理。"""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(frozenset({"github.com", "gitlab.com"}), _default_policy().allowed_git_hosts)
        with patch.dict(os.environ, {"GIT_ALLOWED_HOSTS": " GitLab.com , git.internal.example "}):
            self.assertEqual(frozenset({"gitlab.com", "git.internal.example"}), _default_policy().allowed_git_hosts)
        with patch.dict(os.environ, {"GIT_ALLOWED_HOSTS": ""}):
            self.assertEqual(frozenset(), _default_policy().allowed_git_hosts)

    def test_gitlab_clone_uses_common_acquisition_result_and_cleans_metadata(self):
        """离线替身走完整 Git 获取分支，验证子组 URL、浅克隆策略和元数据清理。"""
        def clone(repo_url, destination, **options):
            root = Path(destination)
            (root / ".git").mkdir()
            (root / ".git/config").write_text("[core]\n", encoding="utf8")
            (root / "main.go").write_text("package main", encoding="utf8")

        url = "https://gitlab.com/group/subgroup/repository.git"
        with tempfile.TemporaryDirectory() as directory:
            workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(directory) / "users"))
            with patch("backend.app.services.project_workspace.sources.git.socket.getaddrinfo", return_value=[
                (2, 1, 6, "", ("93.184.216.34", 443)),
            ]), patch("backend.app.services.project_workspace.sources.git.git.Repo.clone_from", side_effect=clone) as cloned:
                with ProjectImportTransaction(workspace, NoopArtifacts()) as transaction:
                    operation = transaction.begin("test_user")
                    prepared = workspace.prepare(operation, WorkspaceSource.git(url))
                    self.assertEqual(url, prepared.source_tag)
                    self.assertTrue((operation.source_root / "main.go").is_file())
                    self.assertFalse((operation.source_root / ".git").exists())
                    self.assertEqual(0, prepared.sanitize_report.removed_links)
                    cloned.assert_called_once_with(url, str(operation.source_root), depth=1, no_tags=True,
                                                   kill_after_timeout=workspace.policy.git_timeout_seconds)
                self.assertFalse(operation.operation_root.exists())

    def test_zip_filters_packaging_and_escaping_links_without_following_or_materializing(self):
        """合法源码与链接可共存；链接内容不是源码，也不创建工作区外文件。"""
        links = []
        for name in ("repo/packaging/usr/bin/alias", "repo/src/link.py"):
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            links.append(entry)
        with tempfile.TemporaryDirectory() as directory:
            workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(directory) / "users"))
            with ProjectImportTransaction(workspace, NoopArtifacts()) as transaction:
                operation = transaction.begin("test_user")
                prepared = workspace.prepare(operation, WorkspaceSource.zip(zip_bytes({
                    "repo/src/main.py": "print('ok')", links[0]: "binary", links[1]: "../../../../outside.py",
                }), "links.zip"))
                self.assertEqual(2, prepared.sanitize_report.removed_links)
                self.assertEqual(2, prepared.sanitize_report.filtered_out_files)
                self.assertEqual(3, prepared.sanitize_report.scanned_files)
                self.assertEqual("print('ok')", (operation.source_root / "repo/src/main.py").read_text())
                self.assertFalse((operation.source_root / links[0].filename).exists())
                self.assertFalse((operation.source_root / links[1].filename).is_symlink())
                self.assertFalse(any(Path(directory).rglob("outside.py")))
                self.assertFalse((operation.operation_root / "upload.part").exists())
            self.assertFalse(operation.operation_root.exists())

    def test_zip_rejects_unsafe_link_names_and_special_files_with_rollback(self):
        """跳过链接目标不意味着接受危险条目名；FIFO/设备元数据仍失败关闭。"""
        for name, mode in (("../outside.py", stat.S_IFLNK), ("/absolute.py", stat.S_IFLNK),
                           ("C:/outside.py", stat.S_IFLNK), ("repo/pipe", stat.S_IFIFO)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (mode | 0o777) << 16
                workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(directory) / "users"))
                with self.assertRaises(WorkspacePolicyError):
                    with ProjectImportTransaction(workspace, NoopArtifacts()) as transaction:
                        operation = transaction.begin("test_user")
                        workspace.prepare(operation, WorkspaceSource.zip(zip_bytes({entry: "target"}), "unsafe.zip"))
                self.assertFalse(operation.operation_root.exists())

    def test_skipped_links_still_count_toward_archive_entry_budget(self):
        """链接跳过不能绕过 ZIP 条目数限制。"""
        with tempfile.TemporaryDirectory() as directory:
            entry = zipfile.ZipInfo("repo/alias")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            workspace = ProjectWorkspaceService(policy=WorkspacePolicy(max_archive_files=1),
                paths=ProjectWorkspacePaths(Path(directory) / "users"))
            with self.assertRaises(WorkspacePolicyError):
                with ProjectImportTransaction(workspace, NoopArtifacts()) as transaction:
                    operation = transaction.begin("test_user")
                    workspace.prepare(operation, WorkspaceSource.zip(zip_bytes({"main.py": "pass", entry: "main.py"}), "many.zip"))
            self.assertFalse(operation.operation_root.exists())

    def test_zip_traversal_rolls_back_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(directory) / "users"))
            transaction = ProjectImportTransaction(workspace, NoopArtifacts())
            with self.assertRaises(WorkspacePolicyError):
                with transaction:
                    operation = transaction.begin("test_user")
                    workspace.prepare(operation, WorkspaceSource.zip(
                        zip_bytes({"../outside.py": "print('unsafe')"}), "unsafe.zip"
                    ))
            self.assertFalse(any(Path(directory).rglob("outside.py")))
            self.assertFalse(any((Path(directory) / "users" / "test_user" / ".staging").glob("*")))

    def test_zip_extracts_and_sanitizes_regular_project(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = ProjectWorkspaceService(paths=ProjectWorkspacePaths(Path(directory) / "users"))
            transaction = ProjectImportTransaction(workspace, NoopArtifacts())
            with transaction:
                operation = transaction.begin("test_user")
                prepared = workspace.prepare(operation, WorkspaceSource.zip(zip_bytes({
                    "src/main.py": "print('ok')",
                    ".env.local": "TOKEN=secret",
                }), "sample.zip"))
                self.assertEqual("print('ok')", (operation.source_root / "src" / "main.py").read_text())
                self.assertFalse((operation.source_root / ".env.local").exists())
                self.assertEqual(1, prepared.sanitize_report.removed_sensitive_files)
            self.assertFalse(operation.operation_root.exists())


if __name__ == "__main__":
    unittest.main()
