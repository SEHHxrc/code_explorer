"""传播前沿的有界分层采样、完整计数和按需交付回归，不证明安全语义。"""

import unittest

from backend.app.services.security_analysis import SecurityEvidenceCatalog
from backend.app.services.security_analysis.contracts import SecurityEvidencePack
from backend.app.services.security_analysis.flow_analysis.diagnostics import FlowDiagnostics


class FlowDiagnosticTests(unittest.TestCase):
    """高频事件不能吞掉后出现的关键断点；采样不修改事实和传播状态。"""

    def test_flooded_reason_does_not_hide_other_checkpoints(self) -> None:
        diagnostics = FlowDiagnostics(limit=5, per_reason_limit=2)
        for index in range(1000):
            diagnostics.record("instance_allocation_not_modelled", line=index)
        for reason in ("source_seed_not_mapped", "callsite_not_mapped", "sink_without_trackable_value",
                       "established_flows_outside_candidate_budget"):
            diagnostics.record(reason)
        saved = diagnostics.as_dict()
        self.assertEqual(5, len(saved["items"]))
        self.assertEqual(1000, saved["counts"]["instance_allocation_not_modelled"])
        self.assertEqual(999, saved["omitted_counts"]["instance_allocation_not_modelled"])
        self.assertEqual({"source", "call_return", "field_memory", "sink_argument", "evidence_delivery"},
                         {item["checkpoint"] for item in saved["items"]})
        self.assertEqual(1004, sum(saved["checkpoint_counts"].values()))
        self.assertTrue(saved["truncated"])
        self.assertEqual("diagnostic_events_only", saved["coverage"])

    def test_total_capacity_balances_reasons_and_zero_limit_keeps_counts(self) -> None:
        diagnostics = FlowDiagnostics(limit=2)
        for index in range(6):
            diagnostics.record("call_depth_limit", node_id=str(index))
        diagnostics.record("source_seed_not_mapped")
        self.assertEqual({"call_depth_limit", "source_seed_not_mapped"}, {item["reason"] for item in diagnostics.items})
        self.assertEqual(5, diagnostics.as_dict()["omitted_counts"]["call_depth_limit"])
        zero = FlowDiagnostics(limit=0)
        zero.record("future_language_gap")
        self.assertEqual([], zero.items)
        self.assertEqual({"future_language_gap": 1}, zero.as_dict()["counts"])
        self.assertEqual({"unclassified": 1}, zero.as_dict()["checkpoint_counts"])
        for arguments in ({"limit": -1}, {"per_reason_limit": 0}):
            with self.assertRaises(ValueError):
                FlowDiagnostics(**arguments)

    def test_metadata_is_not_a_safety_verdict_or_caller_overridable(self) -> None:
        diagnostics = FlowDiagnostics()
        diagnostics.record("sink_without_trackable_value", checkpoint="source", diagnostic_kind="mapping_gap")
        diagnostics.record("instance_factory_return_not_modelled", reason_detail="return_budget_exceeded")
        self.assertEqual("sink_argument", diagnostics.items[0]["checkpoint"])
        self.assertEqual("untrackable_value", diagnostics.items[0]["diagnostic_kind"])
        self.assertEqual("budget_limit", diagnostics.items[1]["diagnostic_kind"])
        self.assertNotIn("safe", diagnostics.items[0])

    def test_current_and_legacy_diagnostics_are_delivered_without_reconstruction(self) -> None:
        diagnostics = FlowDiagnostics(limit=2)
        for reason in ("source_seed_not_mapped", "sink_without_trackable_value", "call_depth_limit"):
            diagnostics.record(reason)
        for raw in (diagnostics.as_dict(), {"version": "propagation-frontier-v1", "counts": {"old": 1},
                                           "items": [{"reason": "old", "line": 7}], "truncated": False, "limit": 200}):
            pack = SecurityEvidencePack(flow_diagnostics=raw, limitations=["scope-limit"])
            catalog = SecurityEvidenceCatalog(pack.model_dump())
            overview = catalog.detail("_coverage", section="overview")["items"][0]["flow_diagnostics"]
            self.assertEqual({key: value for key, value in raw.items() if key not in {"items", "value_backends"}}, overview)
            offset, delivered = 0, []
            while True:
                page = catalog.detail("_coverage", section="unresolved", offset=offset, limit=1)
                delivered.extend({key: value for key, value in item.items() if key != "kind"}
                                 for item in page["items"] if item["kind"] == "propagation_frontier")
                if not page["has_more"]:
                    break
                offset = page["next_offset"]
            self.assertEqual(raw["items"], delivered)


if __name__ == "__main__":
    unittest.main()
