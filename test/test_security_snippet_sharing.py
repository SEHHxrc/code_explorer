"""共享片段的无损引用、预算闭包、资源身份及观察压缩回归；不调用模型。"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.agents.observations import compact_observation, encoded
from backend.app.core.redaction import redact_secrets
from backend.app.experiments.citations import visible_lines
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidencePromptBuilder
from backend.app.services.security_analysis.slicer import SourceSlicer
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer


class SnippetSharingTests(unittest.TestCase):
    def _pack(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text(
                "from fastapi import FastAPI\nimport os\napp = FastAPI()\n"
                "@app.get('/run')\ndef run(value: str):\n    os.system(value)\n    eval(value)\n", encoding="utf-8")
            analysis = UnifiedCodeAnalyzer(directory).run_full_analysis()
            return SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"])

    def test_repeated_resource_ignores_fact_identity_but_preserves_role_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
            slicer = SourceSlicer(directory, context_lines=2)
            first = slicer.slice_location(path="app.py", line=1, end_line=None, role="source")
            cached = slicer.slice_location(path="app.py", line=1, end_line=None, role="source")
            same_span = slicer.slice_location(path="app.py", line=2, end_line=None, role="source")
            other_role = slicer.slice_location(path="app.py", line=2, end_line=None, role="sink")
            self.assertIs(first, cached)
            self.assertEqual(first.snippet_id, same_span.snippet_id)
            self.assertNotEqual(first.snippet_id, other_role.snippet_id)
            Path(directory, "app.py").write_text("different\ntwo\nthree\n", encoding="utf-8")
            changed = SourceSlicer(directory, context_lines=2).slice_location(path="app.py", line=1, end_line=None, role="source")
            self.assertNotEqual(first.snippet_id, changed.snippet_id)

    def test_shared_table_reconstructs_every_selected_snippet_exactly(self):
        pack = self._pack()
        before = pack.model_dump_json()
        builder = SecurityEvidencePromptBuilder()
        envelope = builder.build(pack, max_chars=100000)
        references = [key for finding in envelope.findings for key in finding.snippet_ids]
        self.assertGreater(len(references), len(envelope.snippets))
        by_id = {item.candidate_id: item for item in pack.candidates}
        for finding in envelope.findings:
            expected = builder._selected_snippets(pack, by_id[finding.finding_id])
            actual = [envelope.snippets[key] for key in finding.snippet_ids]
            self.assertEqual(expected, actual)
        self.assertEqual(before, pack.model_dump_json())
        shared = json.loads(envelope.to_json())
        expanded = json.loads(envelope.to_json())
        resources = expanded.pop("snippets")
        for finding in expanded["findings"]:
            finding["snippets"] = [resources[key] for key in finding.pop("snippet_ids")]
        for observation in expanded["observations"]:
            if observation.get("snippet_id"):
                observation["snippet"] = resources[observation.pop("snippet_id")]
        old_size = len(json.dumps(expanded, ensure_ascii=False, separators=(",", ":")))
        self.assertLess(len(envelope.to_json()), old_size)
        self.assertEqual("1.4", shared["schema_version"])

    def test_budget_pages_have_no_orphans_or_dangling_references(self):
        pack = self._pack()
        for budget in (3000, 7000, 12000, 100000):
            for offset in (0, 1):
                envelope = SecurityEvidencePromptBuilder().build(pack, max_chars=budget, offset=offset, max_findings=1)
                needed = {key for finding in envelope.findings for key in finding.snippet_ids}
                needed.update(item.snippet_id for item in envelope.observations if item.snippet_id)
                self.assertEqual(needed, set(envelope.snippets))
                self.assertLessEqual(len(envelope.to_json()), budget)

    def test_shared_body_counts_as_shown_but_reference_alone_does_not(self):
        envelope = SecurityEvidencePromptBuilder().build(self._pack(), max_chars=100000)
        source = next(iter(envelope.snippets.values()))
        receipt = {"messages": [{"role": "tool", "content": envelope.to_json()}]}
        seen = visible_lines(receipt)
        self.assertIn(source.start_line, seen[source.path])
        payload = json.loads(envelope.to_json())
        payload.pop("snippets")
        self.assertEqual({}, visible_lines({"messages": [{"role": "tool", "content": json.dumps(payload)}]}))

    def test_compaction_keeps_dependency_closure_for_shared_resources(self):
        resource = {"path": "app.py", "start_line": 1, "end_line": 1, "text": "1: short"}
        long = {**resource, "text": "1: " + "x" * 10000}
        observation = {"name": "get_static_security_evidence", "arguments": {}, "result": {"evidence": [], "content": {
            "kind": "static_security_evidence", "schema_version": "1.4",
            "findings": [{"finding_id": "first", "snippet_ids": ["short"]},
                         {"finding_id": "large", "snippet_ids": ["long"]}],
            "observations": [{"fact_id": "other", "snippet_id": "short"}],
            "snippets": {"short": resource, "long": long},
        }}}
        compact = compact_observation(observation, 1800)
        content = compact["result"]["content"]
        self.assertEqual(["first"], [item["finding_id"] for item in content["findings"]])
        self.assertEqual({"short": resource}, content["snippets"])
        self.assertEqual(1, content["omitted_findings"])
        self.assertLessEqual(len(encoded(compact)), 1800)

    def test_shared_redaction_retains_union_of_previous_safety_rules(self):
        values = ["'api_key': 'secret-value'", "token='secret-value'", "sk-abcdefghijklmnop",
                  "-----BEGIN PRIVATE KEY-----\nsecret-value\n-----END PRIVATE KEY-----"]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual("[REDACTED]", redact_secrets(value).strip("'"))


if __name__ == "__main__":
    unittest.main()
