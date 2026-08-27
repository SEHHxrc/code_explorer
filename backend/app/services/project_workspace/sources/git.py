"""经网络和工作区策略约束的 Git 项目来源。"""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import git

from ..exceptions import AcquisitionError, SourceValidationError
from ..filesystem import WorkspaceFilesystem
from ..policy import WorkspacePolicy


def validate_repo_url(repo_url: str, policy: WorkspacePolicy) -> None:
    """拒绝凭据、非 HTTP(S)、非白名单和解析到非公网的仓库地址。"""
    parsed = urlparse(repo_url)
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise SourceValidationError("Only unauthenticated HTTP(S) Git repository URLs are allowed.")
    if policy.allowed_git_hosts and hostname not in policy.allowed_git_hosts:
        raise SourceValidationError("The Git repository host is not allowed by server policy.")
    if hostname in {"localhost", "localhost.localdomain"}:
        raise SourceValidationError("Local repository hosts are not allowed.")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise SourceValidationError("The Git repository host could not be resolved.") from exc
    if not addresses:
        raise SourceValidationError("The Git repository host could not be resolved.")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SourceValidationError("The Git repository host resolved to an invalid address.") from exc
        if not parsed_address.is_global:
            raise SourceValidationError("Private or local repository addresses are not allowed.")


class GitProjectSource:
    """在暂存工作区执行浅克隆，并移除不需要的 Git 元数据。"""

    def __init__(self, policy: WorkspacePolicy, filesystem: WorkspaceFilesystem):
        self.policy = policy
        self.filesystem = filesystem

    def acquire(self, repo_url: str, destination: Path) -> str:
        validate_repo_url(repo_url, self.policy)
        try:
            git.Repo.clone_from(
                repo_url,
                str(destination),
                depth=1,
                no_tags=True,
                kill_after_timeout=self.policy.git_timeout_seconds,
            )
            git_metadata = destination / ".git"
            if git_metadata.exists():
                self.filesystem.remove_child(git_metadata, destination)
        except SourceValidationError:
            raise
        except Exception as exc:
            raise AcquisitionError() from exc
        return repo_url

