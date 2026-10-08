# -*- coding: utf-8 -*-
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.contracts import AgentRunRequest
from backend.app.agents.run_store import AgentRunStore
from backend.app.agents.worker import AgentQueueWorker
from backend.app.models import AgentJobModel, AgentRunModel, Base, ProjectModel
from backend.app.services.projects import ProjectDeletionRepository


def sample_artifact():
    return {
        "manifest": {
            "schema_version": "1.0",
            "project_name": "queue-demo",
            "languages": ["Python"],
            "frameworks": [],
            "package_managers": [],
            "entrypoints": [],
            "build_commands": [],
            "run_commands": [],
            "test_commands": [],
            "modules": [],
            "graph_summary": {},
            "warnings": [],
        },
        "repo_map": "PROJECT queue-demo",
        "overview": "# queue-demo",
        "file_symbols": {},
        "dependency_graph": {"nodes": [], "edges": []},
    }


class AgentQueueTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        self.store = AgentRunStore(self.sessions)

    def create_run(self, run_id="run1", strategy="default"):
        return self.store.create(
            run_id=run_id,
            project_id="project1",
            user_id="user1",
            request=AgentRunRequest(question="项目入口在哪里？", use_model=False),
            strategy=strategy,
        )

    def test_queued_run_is_claimed_by_a_new_store_instance(self):
        self.create_run()
        restarted_store = AgentRunStore(self.sessions)
        claim = restarted_store.claim_next("worker-after-restart")
        self.assertIsNotNone(claim)
        self.assertEqual(claim.run_id, "run1")
        self.assertEqual(restarted_store.get("run1", "user1").status, "running")
        self.assertIsNone(restarted_store.claim_next("competing-worker"))

    def test_selected_model_survives_persistent_queue_claim(self):
        """前端选择的模型应随运行记录持久化并交给独立 Worker。"""
        self.store.create(
            run_id="selected",
            project_id="project1",
            user_id="user1",
            request=AgentRunRequest(
                question="项目入口在哪里？",
                use_model=True,
                model="gpt-selected",
            ),
        )
        claim = AgentRunStore(self.sessions).claim_next("worker-after-restart")
        self.assertEqual(claim.model, "gpt-selected")
        self.assertEqual(self.store.get("selected", "user1").model, "gpt-selected")

    def test_cancel_is_persistent_for_queued_and_running_runs(self):
        self.create_run("queued")
        cancelled = self.store.request_cancel("queued", "user1")
        self.assertEqual(cancelled.status, "cancelled")

        self.create_run("running")
        self.store.claim_next("worker1")
        requested = self.store.request_cancel("running", "user1")
        self.assertEqual(requested.status, "running")
        self.assertTrue(self.store.is_cancel_requested("running"))
        self.assertIn(
            "run.cancel_requested",
            [event.type for event in self.store.events_after("running", 0)],
        )

    def test_expired_worker_lease_finishes_run_safely(self):
        self.create_run()
        self.store.claim_next("dead-worker")
        db = self.sessions()
        db.query(AgentJobModel).filter(AgentJobModel.run_id == "run1").update({
            "updated_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        })
        db.commit()
        db.close()

        recovered = self.store.recover_stale(datetime.now(timezone.utc) - timedelta(seconds=30))
        self.assertEqual(recovered, ["run1"])
        view = self.store.get("run1", "user1")
        self.assertEqual(view.status, "failed")
        self.assertEqual(view.error, "Agent worker lease expired.")

    def test_legacy_run_can_be_cancelled_before_worker_backfill(self):
        db = self.sessions()
        db.add(AgentRunModel(
            id="legacy-cancel",
            project_id="project1",
            user_id="user1",
            question="legacy",
            use_model=False,
            max_steps=1,
            status="running",
        ))
        db.commit()
        db.close()

        view = self.store.request_cancel("legacy-cancel", "user1")
        self.assertEqual(view.status, "running")
        self.assertTrue(self.store.is_cancel_requested("legacy-cancel"))

    def test_project_delete_removes_agent_queue_metadata(self):
        db = self.sessions()
        db.add(ProjectModel(
            id="project1",
            user_id="user1",
            repo_url="upload.zip",
            local_path="controlled",
            file_tree=[],
        ))
        db.commit()
        db.close()
        self.create_run()
        self.store.request_cancel("run1", "user1")

        self.assertTrue(
            ProjectDeletionRepository(self.sessions).delete_owned_with_dependents(
                "project1",
                "user1",
            )
        )
        db = self.sessions()
        self.assertIsNone(db.query(AgentJobModel).filter(AgentJobModel.run_id == "run1").first())
        db.close()

    def test_worker_completes_static_run_from_persistent_queue(self):
        async def scenario():
            self.create_run()
            with tempfile.TemporaryDirectory() as directory:
                projects = SimpleNamespace(
                    get_owned=lambda project_id, user_id: SimpleNamespace(local_path=directory),
                )
                worker = AgentQueueWorker(
                    worker_id="worker1",
                    store=self.store,
                    projects=projects,
                    artifacts=SimpleNamespace(load=lambda project_id: sample_artifact()),
                )
                self.assertTrue(await worker.run_once())
            view = self.store.get("run1", "user1")
            self.assertEqual(view.status, "completed")
            events = [event.type for event in self.store.events_after("run1", 0)]
            self.assertIn("run.started", events)
            self.assertIn("run.completed", events)

        asyncio.run(scenario())

    def test_upgrade_backfills_job_for_legacy_queued_run(self):
        db = self.sessions()
        db.add(AgentRunModel(
            id="legacy",
            project_id="project1",
            user_id="user1",
            question="legacy",
            use_model=False,
            max_steps=1,
            status="queued",
        ))
        db.commit()
        db.close()

        self.assertEqual(self.store.ensure_jobs(), 1)
        claim = self.store.claim_next("worker1")
        self.assertEqual(claim.run_id, "legacy")
        self.assertEqual(claim.strategy, "default")


if __name__ == "__main__":
    unittest.main()
