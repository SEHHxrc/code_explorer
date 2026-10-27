"""公共紧凑协议：逐字段无损、损坏外键拒绝及大类型重复的体积回归。"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest

from backend.app.services.language_backends.go import GoSemanticResolver
from backend.app.services.language_backends.wire import decode_table_result
from backend.app.services.language_backends.worker import SemanticWorkerError, run_worker


def sample() -> dict:
    """生成包含零号外键、未知位置、接收者、零号投影的共同传输样本。"""
    return {
        "wire_format": "tables-v1", "backend": "go", "status": "completed",
        "files": ["main.go"], "type_literals": ["string"],
        "declarations": [[0, 1, 1, 5, 2, "Run", "method", True]],
        "locations": [[0, 1, 1, 5, 2], [0, 2, 3, 2, 8]],
        "calls": [[0, 2, 3, 2, 8, [0], None, "direct"]],
        "types": [[0, 1, 1, 1, 2, "input", 0, "parameter", 0, "callable"]],
        "ssa_functions": [["Run", "method", 0, ["v1"], "v0",
                           {"v0": ["parameter", "self", 0, 0], "v1": ["parameter", "input", 0, None],
                            "v2": ["instruction", "value", 0, 1]},
                           {"b0:i0": ["Field", 0, ["v1"], "v2", 1, 1, "input", ["v1"], "v0", "invoke", 0, ""],
                            "b0:i1": ["Return", 0, ["v2"], None, None, None, "", [], None, "", None, ""]},
                           [[[], [], ["b0:i0", "b0:i1"]]], ["not_a_taint_proof"]]],
    }


class SemanticWireTests(unittest.TestCase):
    """Go/TS 共用同一解码器，不把体积优化变成字段删减或语义改写。"""

    def test_preserves_all_ssa_fields_and_shared_facts(self) -> None:
        payload = sample()
        original = deepcopy(payload)
        result = decode_table_result(payload)
        GoSemanticResolver._validate_ssa(result)
        fn = result.ssa_functions[0]
        inst = fn.instructions["b0:i0"]
        self.assertEqual(payload, original)
        self.assertIs(result.types[0].owner, result.calls[0].declarations[0])
        self.assertEqual(fn.parameters, ["v1"])
        self.assertEqual(fn.receiver_parameter, "v0")
        self.assertEqual(inst.model_dump(), {
            "opcode": "Field", "block": 0, "operands": ["v1"], "result": "v2",
            "location": {"path": "main.go", "line": 2, "column": 3, "end_line": 2, "end_column": 8, "location_id": ""},
            "call_location": {"path": "main.go", "line": 2, "column": 3, "end_line": 2, "end_column": 8, "location_id": ""},
            "source_name": "input", "call_arguments": ["v1"], "receiver_value": "v0",
            "invocation_kind": "invoke", "projection_index": 0, "operator": ""})
        self.assertIsNone(fn.values["v1"].location)
        self.assertIsNone(fn.instructions["b0:i1"].location)
        self.assertEqual(fn.blocks[0].instructions, ["b0:i0", "b0:i1"])
        self.assertEqual(fn.limitations, ["not_a_taint_proof"])

    def test_all_table_references_reject_bool_negative_and_overflow(self) -> None:
        for invalid in (True, -1, 10000):
            for field in ("file", "declaration", "type", "location", "ssa_type"):
                with self.subTest(invalid=invalid, field=field), self.assertRaises(ValueError):
                    payload = sample()
                    if field == "file": payload["calls"][0][0] = invalid
                    elif field == "declaration": payload["calls"][0][5] = [invalid]
                    elif field == "type": payload["types"][0][6] = invalid
                    elif field == "location": payload["ssa_functions"][0][2] = invalid
                    else: payload["ssa_functions"][0][5]["v0"][2] = invalid
                    decode_table_result(payload)

    def test_wrong_row_length_is_rejected(self) -> None:
        for table in ("calls", "types", "declarations", "locations", "ssa_functions"):
            with self.subTest(table=table), self.assertRaises(ValueError):
                payload = sample()
                payload[table][0].append("unexpected")
                decode_table_result(payload)

    def test_variable_reference_extension_is_shared_and_validated(self) -> None:
        """变量声明共享表允许未知类型，零号外键有效；旧 tables-v1 无扩展仍可读取。"""
        payload = sample()
        payload["variable_declarations"] = [[0, 1, 1, 1, 2, "input", "parameter", 0, "callable"]]
        payload["variable_references"] = [[0, 2, 3, 2, 8, "input", [0], None, 0, "callable"]]
        original = deepcopy(payload)
        result = decode_table_result(payload)
        self.assertIs(result.variable_references[0].declarations[0], result.variable_declarations[0])
        self.assertEqual(original, payload)
        self.assertEqual([], decode_table_result(sample()).variable_references)
        for invalid in (True, -1, 999):
            for field in ("target", "owner"):
                with self.subTest(invalid=invalid, field=field), self.assertRaises(ValueError):
                    broken = deepcopy(payload)
                    if field == "target": broken["variable_references"][0][6] = [invalid]
                    else: broken["variable_declarations"][0][7] = invalid
                    decode_table_result(broken)
        for field in ("variable_declarations", "variable_references"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                broken = deepcopy(payload)
                broken[field][0].append("unexpected")
                decode_table_result(broken)

    def test_large_repeated_types_fit_without_changing_output_guard(self) -> None:
        payload = sample()
        literal = "struct { " + "Field string; " * 1000 + "}"
        payload["type_literals"] = [literal]
        payload["ssa_functions"][0][5] = {
            **payload["ssa_functions"][0][5],
            **{f"extra{index}": ["constant", "", 0, None] for index in range(6000)},
        }
        # 旧逐值展开仅类型文本就超过 64 MiB，不在测试中真的分配 80+ MiB JSON。
        self.assertGreater(len(literal) * 6000, 64 * 1024 * 1024)
        self.assertLess(len(json.dumps(payload)), 1024 * 1024)
        result = decode_table_result(payload)
        self.assertEqual(len(result.ssa_functions[0].values), 6003)
        self.assertIs(result.ssa_functions[0].values["extra0"].type_literal,
                      result.ssa_functions[0].values["extra5999"].type_literal)

    def test_worker_output_guard_still_rejects_oversized_response(self) -> None:
        with self.assertRaisesRegex(SemanticWorkerError, "semantic_output_limit"):
            run_worker([sys.executable, "-c", "print('{}' * 100)"], {}, directory=Path.cwd(),
                       environment=dict(os.environ), timeout_seconds=5, max_output_bytes=100)


if __name__ == "__main__":
    unittest.main()
