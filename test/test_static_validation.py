"""只读真实项目验证器的离线契约回归，不依赖上传项目或模型。"""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from test.validate_static_analysis import validate


class StaticValidationTests(unittest.TestCase):
    """新诊断可复现，不能覆盖既有产物或向待分析项目写入文件。"""

    def test_validation_preserves_source_and_records_production_coverage(self) -> None:
        """完整调用生产流水线，核对模型交付不改写目标源码及原实验记录。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "sample")
            root.mkdir()
            source = root / "app.py"
            content = "import os\ndef run():\n value=input()\n os.system(value)\n"
            source.write_text(content, encoding="utf8")
            output = Path(directory, "new-diagnostic.json")
            with contextlib.redirect_stdout(io.StringIO()):
                report = validate(root, output)
            self.assertEqual(content, source.read_text(encoding="utf8"))
            self.assertEqual([source], list(root.iterdir()))
            self.assertEqual(report, json.loads(output.read_text(encoding="utf8")))
            self.assertTrue(report["source_unchanged"])
            self.assertTrue(report["implementation_unchanged"])
            self.assertEqual(64, len(report["implementation_sha256"]))
            self.assertEqual(1, report["program_graph"]["coverage"]["files_parsed"])
            self.assertEqual([], report["checks"]["mechanical_errors"])
            self.assertEqual("not_proven", report["checks"]["semantic_correctness"])
            old = output.read_bytes()
            with self.assertRaises(ValueError):
                validate(root, output)
            self.assertEqual(old, output.read_bytes())
            with self.assertRaises(ValueError):
                validate(root, root / "diagnostic.json")
