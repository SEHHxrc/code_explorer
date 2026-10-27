import { pathFamilyColor, SHARED_PATH_COLOR, UNPAIRED_OPERATION_COLOR } from '../../dependency-graph/graphStyle.js'

export const pathKindLabel = (kind) => ({
  propagation_path: '值传播路径', structural_lead: '结构调用线索', operation_lead: '未建立传播的操作线索',
  unpaired_operation: '未配对敏感操作（非路径）',
})[kind] || kind

export const pathLocation = (point) => point ? `${point.path}:${point.line}${point.column ? `:${point.column}` : ''}` : '位置不可用'

/** 完整目录决定族序号和颜色；选路、路线顺序变化都不重新分配。旧载荷无分组时按独立路线处理。 */
export const pathFamilyId = (path) => path.path_group_id || path.id
export const pathFamilies = (paths = []) => {
  const counts = new Map()
  for (const path of paths) {
    const id = pathFamilyId(path)
    counts.set(id, (counts.get(id) || 0) + 1)
  }
  return [...counts.keys()].sort().map((id, index) => ({
    id, label: `路径族 ${index + 1}`, color: pathFamilyColor(index), routeCount: counts.get(id),
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
  const nodeIds = route ? new Set(route.node_ids) : operation ? new Set([operation.node_id]) : null
  const edgeIds = route ? new Set(route.edge_ids) : operation ? new Set() : null
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
    hasSharedFamilies: [...members.values(), ...edgeMembers.values()].some((item) => item.families.size > 1),
    nodes: (data?.graph?.nodes || []).filter((node) => !nodeIds || nodeIds.has(node.id)).map((node) => {
      const style = memberStyle(members.get(node.id))
      const observed = operationsByNode.get(node.id) || []
      return { ...node, security: { ...style, operations: observed,
        color: style.color || (observed.length ? UNPAIRED_OPERATION_COLOR : undefined),
        roles: [...new Set([...(roles.get(node.id) || []), ...(observed.length ? ['Sink'] : [])])],
      } }
    }),
    edges: (data?.graph?.edges || []).filter((edge) => !edgeIds || edgeIds.has(edge.id)).map((edge) => ({
      ...edge, security: memberStyle(edgeMembers.get(edge.id)),
    })),
  }
}
