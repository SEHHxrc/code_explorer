import { pathFamilyColor, SHARED_PATH_COLOR, UNPAIRED_OPERATION_COLOR } from '../../dependency-graph/graphStyle.js'

export const pathKindLabel = (kind) => ({
  propagation_path: '值传播路径', structural_lead: '结构调用线索', operation_lead: '未建立传播的操作线索',
  unpaired_operation: '未配对敏感操作（非路径）',
})[kind] || kind

export const pathLocation = (point) => point ? `${point.path}:${point.line}${point.column ? `:${point.column}` : ''}` : '位置不可用'
export const connectionStatusLabel = (status) => ({ modelled: '模型内绑定', possible: '保守绑定', unresolved: '目标绑定未确认' })[status] || '未知'

export const boundaryReasonLabel = (reason) => ({
  source_to_operation_not_established: '尚未建立输入到此操作的值传播',
  mapping_keys_or_duplicates_unknown: '字典键存在性、重复键或展开有效性未证明',
  dynamic_member_name_unknown: '动态成员名未知',
  dynamic_receiver_type_unknown: '动态接收者类型未知',
  dynamic_callable_binding_unknown: '可调用值来源或接收者不唯一',
  dynamic_target_limit: '动态目标达到有界上限',
  dynamic_bound_receiver_changed: '绑定后接收者变量已改变，未复用其新对象字段',
  duplicate_keyword_argument: '已观察到重复关键字，不能建立该调用的参数传播',
})[reason] || '尚未建模的调用绑定边界'

/** 完整目录决定族序号和颜色；选路、路线顺序变化都不重新分配。旧载荷无分组时按独立路线处理。 */
export const pathFamilyId = (path) => path.path_group_id || path.id
const endpointFunction = (endpoint) => {
  const parts = (endpoint?.symbol || '').split('::')
  return (parts.length > 1 ? parts.slice(1).join('.') : parts[0]) || '未知函数'
}
export const pathFamilies = (paths = []) => {
  const groups = new Map()
  for (const path of paths) {
    const id = pathFamilyId(path)
    if (!groups.has(id)) groups.set(id, { title: `${endpointFunction(path.source)} → ${endpointFunction(path.sink)}`, count: 0 })
    groups.get(id).count++
  }
  return [...groups.keys()].sort().map((id, index) => ({
    id, label: `路径族 ${index + 1}`, color: pathFamilyColor(index), routeCount: groups.get(id).count,
    title: groups.get(id).title,
  }))
}

/** 同一成员在一条路线中只计一次；共享关系不复制为多条渲染边。 */
const addMembers = (members, ids, family) => {
  for (const id of new Set(ids)) {
    if (!members.has(id)) members.set(id, { count: 0, families: new Set() })
    const item = members.get(id)
    item.count++
    item.families.add(family)
  }
}
const memberStyle = (member) => {
  const families = [...(member?.families || [])].sort((a, b) => a.id.localeCompare(b.id))
  const shared = families.length > 1
  return { pathCount: member?.count || 0, families, shared, color: shared ? SHARED_PATH_COLOR : families[0]?.color }
}

/** 展示候选并集和未配对操作；按 ID 选路/操作，独立操作不推断 Source、族或调用边。 */
export const securityGraph = (data, selectedId = '') => {
  const families = pathFamilies(data?.paths)
  const byFamily = new Map(families.map((family) => [family.id, family]))
  const route = data?.paths?.find((item) => item.id === selectedId)
  const operation = data?.operations?.find((item) => item.id === selectedId)
  const nodeIds = route ? new Set(route.node_ids) : operation ? new Set([operation.node_id, ...(operation.context_node_ids || [])]) : null
  const edgeIds = route ? new Set(route.edge_ids) : operation ? new Set(operation.context_edge_ids || []) : null
  const structure = new Set(data?.structural_edge_ids || [])
  const possible = new Set(route ? route.possible_edge_ids || [] : data?.possible_edge_ids || [])
  const boundaries = (data?.boundaries || []).filter((item) => !nodeIds || nodeIds.has(item.symbol))
    .map((item) => ({ ...item, labels: item.reasons.map(boundaryReasonLabel) }))
  const members = new Map()
  const edgeMembers = new Map()
  const roles = new Map()
  const operations = route ? [] : operation ? [operation] : data?.operations || []
  const operationsByNode = new Map()
  for (const item of operations) {
    if (!operationsByNode.has(item.node_id)) operationsByNode.set(item.node_id, [])
    operationsByNode.get(item.node_id).push(item)
  }
  for (const path of route ? [route] : operation ? [] : data?.paths || []) {
    const family = byFamily.get(pathFamilyId(path))
    addMembers(members, path.node_ids, family)
    addMembers(edgeMembers, path.edge_ids, family)
    for (const [role, endpoint] of [['Source', path.source], ['Sink', path.sink]]) {
      if (!roles.has(endpoint.symbol)) roles.set(endpoint.symbol, new Set())
      roles.get(endpoint.symbol).add(role)
    }
  }
  return {
    default_preset: 'all',
    pathFamilies: route ? [byFamily.get(pathFamilyId(route))] : operation ? [] : families,
    operationCount: operations.length,
    boundaryCount: boundaries.length,
    structureCount: operation ? edgeIds.size : structure.size,
    possibleCount: (data?.graph?.edges || []).filter((edge) => possible.has(edge.id) && (!edgeIds || edgeIds.has(edge.id))).length,
    hasSharedFamilies: [...members.values(), ...edgeMembers.values()].some((item) => item.families.size > 1),
    nodes: (data?.graph?.nodes || []).filter((node) => !nodeIds || nodeIds.has(node.id)).map((node) => {
      const style = memberStyle(members.get(node.id))
      const observed = operationsByNode.get(node.id) || []
      return { ...node, security: { ...style, operations: observed,
        boundaries: boundaries.filter((item) => item.symbol === node.id),
        color: style.color || (observed.length ? UNPAIRED_OPERATION_COLOR : '#94a3b8'),
        roles: [...new Set([...(roles.get(node.id) || []), ...(observed.length ? ['Sink'] : [])])],
      } }
    }),
    edges: (data?.graph?.edges || []).filter((edge) => !edgeIds || edgeIds.has(edge.id)).map((edge) => {
      const structural = !edgeMembers.has(edge.id) && (structure.has(edge.id) || Boolean(operation))
      return { ...edge, security: { ...memberStyle(edgeMembers.get(edge.id)), structural, possible: possible.has(edge.id),
        ...(structural ? { color: '#94a3b8' } : {}),
      } }
    }),
  }
}
