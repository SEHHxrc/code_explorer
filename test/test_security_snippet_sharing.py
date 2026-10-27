"""共享片段的无损引用、预算闭包、资源身份及观察压缩回归；不调用模型。"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.agents.observations import compact_observation, encoded
from backend.app.core.redaction import redact_secrets
from backend.app.experiments.citations import visible_lines
from backend.app.services.security_analysis import SecurityAnalysisService, SecurityEvidenceCatalog
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
        catalog = SecurityEvidenceCatalog(pack.model_dump())
        references = [key for item in pack.candidates for key in item.snippet_ids]
        self.assertGreater(len(references), len(set(references)))
        for candidate in pack.candidates:
            detail = catalog.detail(candidate.candidate_id, section="snippets", limit=10)
            expected = [pack.snippets[key].model_dump() for key in candidate.snippet_ids]
            actual = [detail["snippets"][item["snippet_id"]] for item in detail["items"]]
            self.assertEqual(expected, actual)
        self.assertEqual(before, pack.model_dump_json())
        self.assertNotIn('"snippets"', catalog.summary())

    def test_budget_pages_have_no_orphans_or_dangling_references(self):
        pack = self._pack()
        catalog = SecurityEvidenceCatalog(pack.model_dump())
        for candidate in pack.candidates:
            for offset in range(len(candidate.snippet_ids) + 1):
                detail = catalog.detail(candidate.candidate_id, section="snippets", offset=offset, limit=1)
                self.assertEqual({item["snippet_id"] for item in detail["items"]}, set(detail["snippets"]))
                self.assertLessEqual(len(encoded(detail)), 24000)

    def test_shared_body_counts_as_shown_but_reference_alone_does_not(self):
        pack = self._pack()
        payload = SecurityEvidenceCatalog(pack.model_dump()).detail(pack.candidates[0].candidate_id, section="snippets")
        source = next(iter(payload["snippets"].values()))
        receipt = {"messages": [{"role": "tool", "content": encoded(payload)}]}
        seen = visible_lines(receipt)
        self.assertIn(source["start_line"], seen[source["path"]])
        payload.pop("snippets")
        self.assertEqual({}, visible_lines({"messages": [{"role": "tool", "content": json.dumps(payload)}]}))

    def test_compaction_keeps_dependency_closure_for_shared_resources(self):
        resource = {"path": "app.py", "start_line": 1, "end_line": 1, "text": "1: short"}
        long = {**resource, "text": "1: " + "x" * 10000}
        observation = {"name": "get_static_security_evidence", "arguments": {}, "result": {"evidence": [], "content": {
            "kind": "static_security_evidence", "view": "detail",
            "items": [{"snippet_id": "short"}, {"snippet_id": "long"}],
            "snippets": {"short": resource, "long": long},
        }}}
        compact = compact_observation(observation, 1800)
        content = compact["result"]["content"]
        self.assertEqual(["short"], [item["snippet_id"] for item in content["items"]])
        self.assertEqual({"short": resource}, content["snippets"])
        self.assertEqual(1, content["omitted_items"])
        self.assertLessEqual(len(encoded(compact)), 1800)

    def test_shared_redaction_retains_union_of_previous_safety_rules(self):
        values = ["'api_key': 'secret-value'", "token='secret-value'", "sk-abcdefghijklmnop",
                  "-----BEGIN PRIVATE KEY-----\nsecret-value\n-----END PRIVATE KEY-----"]
        for value in values:
            with self.subTest(value=value):
                self.assertIn("[REDACTED]", redact_secrets(value))
                self.assertNotIn("secret-value", redact_secrets(value))
                self.assertEqual(value.count('\n'), redact_secrets(value).count('\n'))


if __name__ == "__main__":
    unittest.main()
