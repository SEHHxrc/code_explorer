"""公共调用后置操作：仅编排 CFG 时序，既不产生 Source 也不推断参数或副作用。"""

import hashlib

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphEdge, ProgramGraphNode


def normal_exit_values(function: FunctionProgramGraph, names: set[str]) -> tuple[FunctionProgramGraph, str]:
    """输入函数与出口读取槽，输出独立的正常退出值节点/CFG；异常仍直达原出口，不假定执行成功。

    只增加值观察点，不产生定义、Source 或调用关系。重复应用合并读取槽，不复制出口节点；
    无正常路径时该节点保持不可达，消费者必须验证可达性。RD/DFG 由调用方一次重算。
    """
    identity = "normal-values:" + function.method_id
    nodes = dict(function.nodes)
    edges = [edge for edge in function.edges if edge.kind == "cfg"]
    if identity not in nodes:
        nodes[identity] = ProgramGraphNode(node_id=identity, kind="method_exit", language=function.language,
            method_id=function.method_id, location=function.location, code="normal_exit_values (generated)", provenance="inferred")
        normal = [edge for edge in edges if edge.target == function.exit_node_id and edge.branch != "exception"]
        replaced = {edge.edge_id for edge in normal}
        edges = [edge for edge in edges if edge.edge_id not in replaced]
        edges.extend(edge.model_copy(update={"target": identity, "edge_id": edge.edge_id + ":normal-values"}) for edge in normal)
        if normal:
            edges.append(ProgramGraphEdge(edge_id=identity + ":cfg", source=identity,
                target=function.exit_node_id, kind="cfg", branch="normal"))
    node = nodes[identity]
    nodes[identity] = node.model_copy(update={"uses": sorted(set(node.uses) | names)})
    return function.model_copy(update={"nodes": nodes, "edges": edges}), identity


def append_post_operations(
    function: FunctionProgramGraph, operations: dict[str, list[ProgramGraphNode]],
) -> FunctionProgramGraph:
    """输入原调用节点到后置操作序列，返回新 CFG；异常边绕过后置写入，原图不修改。

    调用参数与接收者仍在原节点读取；后置定义仅在调用正常返回后生效。
    调用无正常出边时不凭空建立后置路径；不重算 RD/DFG，由调用方一次性重算。
    """
    nodes = dict(function.nodes)
    edges = [edge for edge in function.edges if edge.kind == "cfg"]
    for original, sequence in operations.items():
        outgoing = [edge for edge in edges if edge.source == original and edge.branch != "exception"]
        if not sequence or not outgoing:
            continue
        if original not in nodes or any(item.node_id in nodes or item.method_id != function.method_id for item in sequence):
            raise ValueError("Post-call operations require fresh nodes in the same function")
        if len({item.node_id for item in sequence}) != len(sequence):
            raise ValueError("Post-call operation identities must be unique")
        nodes.update((item.node_id, item) for item in sequence)
        replaced = {edge.edge_id for edge in outgoing}
        edges = [edge for edge in edges if edge.edge_id not in replaced]
        edges.extend(edge.model_copy(update={"source": sequence[-1].node_id, "edge_id": edge.edge_id + ":post"}) for edge in outgoing)
        for source, target in zip([original, *(item.node_id for item in sequence)], [item.node_id for item in sequence]):
            key = hashlib.sha256((source + "\x1f" + target).encode()).hexdigest()[:24]
            edges.append(ProgramGraphEdge(edge_id="post-cfg:" + key, source=source, target=target, kind="cfg", branch="normal"))
    return function.model_copy(update={"nodes": nodes, "edges": edges})
