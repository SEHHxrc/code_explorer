# -*- coding: utf-8 -*-
"""项目数据管理库存与前端快照恢复测试。"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.app.services.project_analysis.repository import ProjectRecord
from backend.app.services.project_inventory import ProjectInventoryError, ProjectInventoryService
from backend.app.services.project_workspace.paths import ProjectWorkspacePaths


def sample_artifact() -> dict:
    """返回可恢复为前端交换格式的最小分析产物。"""
    return {
        "manifest": {
            "schema_version": "1.0",
            "project_name": "inventory-demo",
            "languages": ["Python"],
            "frameworks": ["FastAPI"],
            "package_managers": ["pip"],
            "entrypoints": [],
            "build_commands": [],
            "run_commands": [],
            "test_commands": [],
            "modules": [],
            "graph_summary": {},
            "warnings": [],
        },
        "overview": "# inventory-demo",
        "dependency_graph": {
            "nodes": [{
                "id": "main.py", "name": "main.py", "level": "module", "file": "main.py",
            }],
            "links": [],
        },
    }


class FakeProjects:
    """提供库存服务测试所需的最小项目仓储。"""

    def __init__(self, record: ProjectRecord) -> None:
        """保存唯一项目记录。"""
        self.record = record

    def list_owned(self, user_id: str) -> list[ProjectRecord]:
        """返回匹配用户的项目列表。"""
        return [self.record] if user_id == self.record.user_id else []

    def get_owned(self, project_id: str, user_id: str) -> ProjectRecord | None:
        """返回匹配用户和项目的记录。"""
        return self.record if (project_id, user_id) == (self.record.project_id, self.record.user_id) else None

    def has_active_runs(self, project_id: str, user_id: str) -> bool:
        """测试项目没有活动任务。"""
        return False


class ProjectInventoryTests(unittest.TestCase):
    """验证后端孤立项目可见、可统计并可恢复。"""

    def test_lists_storage_and_restores_snapshot(self) -> None:
        """库存应统计源码和产物，并返回前端可接受的恢复数据。"""
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectWorkspacePaths(Path(directory) / "users")
            workspace = paths.project_root("user-1", "project-1")
            workspace.mkdir(parents=True)
            (workspace / "main.py").write_bytes(b"12345")
            record = ProjectRecord(
                project_id="project-1",
                user_id="user-1",
                source="local_upload://demo.zip",
                local_path=str(workspace),
                file_tree=[{"name": "main.py", "path": "main.py", "type": "file"}],
                created_at=datetime.now(timezone.utc),
            )
            artifact = sample_artifact()
            service = ProjectInventoryService(
                projects=FakeProjects(record),
                paths=paths,
                artifact_loader=lambda _: artifact,
                artifact_sizer=lambda _: 7,
            )
            inventory = asyncio.run(service.list("user-1"))
            self.assertEqual(inventory["project_count"], 1)
            self.assertEqual(inventory["total_bytes"], 12)
            self.assertTrue(inventory["projects"][0]["artifact_readable"])

            snapshot = asyncio.run(service.snapshot("project-1", "user-1"))
            self.assertEqual(snapshot["project_manifest"]["project_name"], "inventory-demo")
            self.assertEqual(snapshot["dependency_graph"]["summary"]["node_count"], 1)
            self.assertEqual(snapshot["file_tree"][0]["path"], "main.py")

    def test_missing_workspace_is_reported_as_stale(self) -> None:
        """数据库记录仍在但源码丢失时，应拒绝恢复并允许前端清理。"""
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectWorkspacePaths(Path(directory) / "users")
            record = ProjectRecord(
                project_id="project-1",
                user_id="user-1",
                source="test",
                local_path="ignored",
                file_tree=[],
                created_at=datetime.now(timezone.utc),
            )
            service = ProjectInventoryService(
                projects=FakeProjects(record),
                paths=paths,
                artifact_loader=lambda _: sample_artifact(),
                artifact_sizer=lambda _: 7,
            )
            with self.assertRaises(ProjectInventoryError) as caught:
                asyncio.run(service.snapshot("project-1", "user-1"))
            self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
