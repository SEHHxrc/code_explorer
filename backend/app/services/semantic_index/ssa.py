"""原生 SSA 交换契约的公共结构校验；不校验安全含义或求解内存/污点。"""

from .contracts import SSAFunctionFact


def validate_ssa_function(function: SSAFunctionFact) -> None:
    """校验参数、块、指令和值外键；生产接入与历史恢复共用，损坏时抛稳定 ValueError 码。"""
    parameters = [*function.parameters, *([function.receiver_parameter] if function.receiver_parameter else [])]
    if any(value not in function.values for value in parameters):
        raise ValueError("invalid_native_parameter_reference")
    listed, results = set(), set()
    for index, block in enumerate(function.blocks):
        for target in block.successors:
            if not 0 <= target < len(function.blocks) or index not in function.blocks[target].predecessors:
                raise ValueError("invalid_native_block_reference")
        for source in block.predecessors:
            if not 0 <= source < len(function.blocks) or index not in function.blocks[source].successors:
                raise ValueError("invalid_native_block_reference")
        for key in block.instructions:
            instruction = function.instructions.get(key)
            if instruction is None or key in listed or instruction.block != index:
                raise ValueError("invalid_native_instruction_table")
            listed.add(key)
            values = [*instruction.operands, *instruction.call_arguments,
                      *([instruction.result] if instruction.result else []),
                      *([instruction.receiver_value] if instruction.receiver_value else [])]
            if any(value not in function.values for value in values):
                raise ValueError("invalid_native_value_reference")
            if instruction.result is not None:
                if instruction.result in results:
                    raise ValueError("invalid_native_result_identity")
                results.add(instruction.result)
            if instruction.opcode == "Phi" and len(instruction.operands) != len(block.predecessors):
                raise ValueError("invalid_native_phi")
    if listed != function.instructions.keys():
        raise ValueError("invalid_native_instruction_table")
