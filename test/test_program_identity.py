# -*- coding: utf-8 -*-
"""Shared program identity regression tests."""

import unittest

from backend.app.services.program_index import ProgramIdentity


class ProgramIdentityTests(unittest.TestCase):
    """Verify dependency, security IR and future data flow can share stable IDs."""

    def test_file_and_symbol_ids_use_project_relative_posix_paths(self):
        """Path separators normalize without erasing parent traversal markers."""
        self.assertEqual("pkg/app.py", ProgramIdentity.file_id(".\\pkg\\app.py"))
        self.assertEqual("../app.py", ProgramIdentity.file_id("../app.py"))
        self.assertEqual(
            "pkg/app.py::Service::run",
            ProgramIdentity.symbol_id("pkg\\app.py", ["Service", "run"]),
        )

    def test_callsite_identity_depends_only_on_source_range(self):
        """Resolved targets must not participate in callsite identity."""
        first = ProgramIdentity.callsite_id("app.py", 10, 5, 10, 20)
        second = ProgramIdentity.callsite_id(".\\app.py", 10, 5, 10, 20)
        moved = ProgramIdentity.callsite_id("app.py", 11, 5, 11, 20)
        self.assertEqual(first, second)
        self.assertNotEqual(first, moved)


if __name__ == "__main__":
    unittest.main()
