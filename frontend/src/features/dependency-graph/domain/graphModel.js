import Graph from 'graphology'
import {
  DYNAMIC_CALL_COLOR,
  LEVEL_ALIASES,
  edgeStyle,
  edgeWeight,
  nodeColor,
  nodeSize,
} from '../graphStyle.js'

const structuralRelations = new Set(['contains', 'declares'])

/** Normalize the current legacy payload in one place and build the canonical client graph. */
export const createGraph = (payload = {}) => {
  const rawNodes = payload.nodes || []
  const rawEdges = payload.edges || []
  const graph = new Graph({ type: 'directed', multi: false, allowSelfLoops: false })
  const levelCounts = {}
  const relationCounts = {}

  for (const node of rawNodes) {
    const id = node?.id == null ? '' : String(node.id)
    if (!id || graph.hasNode(id)) continue
    const level = LEVEL_ALIASES[node.level] || node.level || 'variable'
    const color = nodeColor({ ...node, level })
    levelCounts[level] = (levelCounts[level] || 0) + 1
    graph.addNode(id, {
      label: node.name || id.split('::').pop(),
      level,
      kind: node.kind || node.type || level,
      file: node.file || '',
      line: Number(node.line) || 0,
      lang: node.lang || '',
      baseColor: color,
      color,
      size: 3,
      x: 0,
      y: 0,
      hidden: false,
      pinned: false,
    })
  }

  for (const edge of rawEdges) {
    const source = String(typeof edge.source === 'object' ? edge.source?.id ?? '' : edge.source ?? '')
    const target = String(typeof edge.target === 'object' ? edge.target?.id ?? '' : edge.target ?? '')
    if (!graph.hasNode(source) || !graph.hasNode(target) || source === target) continue
    if (graph.hasEdge(source, target)) continue
    const relation = edge.relation || edge.type || 'calls'
    const style = edgeStyle(relation)
    const dynamic = edge.dispatch === 'dynamic'
    relationCounts[relation] = (relationCounts[relation] || 0) + 1
    graph.addDirectedEdge(source, target, {
      relation,
      dispatch: edge.dispatch || '',
      baseColor: dynamic && relation === 'calls' ? DYNAMIC_CALL_COLOR : style.color,
      color: style.color,
      baseWidth: style.width,
      size: style.width,
      weight: edgeWeight(relation),
      type: 'curved',
      hidden: false,
    })
  }

  const ranked = []
  graph.forEachNode((id, attrs) => {
    const degree = graph.degree(id)
    const size = nodeSize(attrs, graph.order)
    graph.mergeNodeAttributes(id, { degree, baseSize: size, size })
    ranked.push({ id, degree, noisy: ['builtin', 'stdlib', 'external'].includes(attrs.level) })
  })
  const labelLimit = Math.min(20, Math.max(8, Math.ceil(Math.sqrt(graph.order))))
  ranked.filter((item) => !item.noisy)
      .sort((a, b) => b.degree - a.degree)
      .slice(0, labelLimit)
      .forEach((item) => graph.setNodeAttribute(item.id, 'forceLabel', true))

  seedPositions(graph, collectParents(graph))
  return {
    graph,
    summary: {
      nodeCount: graph.order,
      edgeCount: graph.size,
      levelCounts,
      relationCounts,
      maxDegree: ranked.reduce((value, item) => Math.max(value, item.degree), 0),
    },
  }
}

/** Return the first structural parent of every contained/declared node. */
export const collectParents = (graph) => {
  const parents = new Map()
  graph.forEachEdge((_id, attrs, source, target) => {
    if (structuralRelations.has(attrs.relation) && !parents.has(target)) parents.set(target, source)
  })
  return parents
}

/** Seed stable root positions and place members close to their structural parent. */
export const seedPositions = (graph, parents = collectParents(graph)) => {
  const count = graph.order
  const spread = Math.sqrt(count) * 45
  const golden = Math.PI * (3 - Math.sqrt(5))
  const roots = []
  const rest = []
  graph.forEachNode((id) => (parents.has(id) ? rest.push(id) : roots.push(id)))
  roots.forEach((id, index) => {
    const radius = spread * Math.sqrt((index + 1) / Math.max(roots.length, 1))
    const angle = index * golden
    graph.mergeNodeAttributes(id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius })
  })

  const jitter = Math.max(12, Math.sqrt(count) * 2.5)
  const placed = new Set(roots)
  let pending = rest
  let guard = 0
  while (pending.length && guard++ < 12) {
    const next = []
    for (const id of pending) {
      const parent = parents.get(id)
      if (!parent || !placed.has(parent)) { next.push(id); continue }
      // The id-derived angle keeps rebuilds stable without forcing every member onto one ray.
      const hash = [...id].reduce((value, char) => ((value * 31) + char.charCodeAt(0)) >>> 0, 7)
      const angle = (hash % 6283) / 1000
      const radius = jitter * (0.4 + ((hash % 100) / 100) * 0.8)
      graph.mergeNodeAttributes(id, {
        x: graph.getNodeAttribute(parent, 'x') + Math.cos(angle) * radius,
        y: graph.getNodeAttribute(parent, 'y') + Math.sin(angle) * radius,
      })
      placed.add(id)
    }
    if (next.length === pending.length) break
    pending = next
  }
  pending.forEach((id, index) => {
    const angle = index * golden
    graph.mergeNodeAttributes(id, { x: Math.cos(angle) * spread * 1.2, y: Math.sin(angle) * spread * 1.2 })
  })
}

/** Copy only visible nodes and edges for Sigma and ForceAtlas2. */
export const createVisibleGraph = (source) => {
  const visible = new Graph({ type: 'directed', multi: false, allowSelfLoops: false })
  source.forEachNode((id, attrs) => {
    if (!attrs.hidden) visible.addNode(id, { ...attrs, hidden: false })
  })
  source.forEachEdge((id, attrs, sourceId, targetId) => {
    if (!attrs.hidden && visible.hasNode(sourceId) && visible.hasNode(targetId)) {
      visible.addDirectedEdgeWithKey(id, sourceId, targetId, { ...attrs, hidden: false })
    }
  })
  return visible
}

/** Apply filters to the full graph and return a fresh visible graph plus counts. */
export const filterGraph = (graph, options) => {
  const {
    levels, relations, minDegree, manuallyHidden,
    selectedId = null, focusMode = false,
  } = options
  const structuralLevels = new Set(['module', 'external_module', 'stdlib_module'])
  const focusNodes = new Set()
  if (focusMode && selectedId && graph.hasNode(selectedId)) {
    focusNodes.add(selectedId)
    graph.forEachNeighbor(selectedId, (id) => focusNodes.add(id))
  }
  const candidates = new Map()
  graph.forEachNode((id, attrs) => {
    candidates.set(id, id === selectedId || (
      levels.has(attrs.level) && !manuallyHidden.has(id) && (!focusMode || focusNodes.has(id))
    ))
  })
  const filteredDegrees = new Map()
  graph.forEachEdge((_id, attrs, source, target) => {
    if (!relations.has(attrs.relation) || !candidates.get(source) || !candidates.get(target)) return
    filteredDegrees.set(source, (filteredDegrees.get(source) || 0) + 1)
    filteredDegrees.set(target, (filteredDegrees.get(target) || 0) + 1)
  })
  let nodeCount = 0
  let edgeCount = 0
  graph.forEachNode((id, attrs) => {
    const belowThreshold = !structuralLevels.has(attrs.level)
      && id !== selectedId && (filteredDegrees.get(id) || 0) < minDegree
    const hidden = !candidates.get(id) || belowThreshold
    graph.setNodeAttribute(id, 'hidden', hidden)
    if (!hidden) nodeCount += 1
  })
  graph.forEachEdge((id, attrs, source, target) => {
    const hidden = !relations.has(attrs.relation)
      || graph.getNodeAttribute(source, 'hidden') || graph.getNodeAttribute(target, 'hidden')
    graph.setEdgeAttribute(id, 'hidden', hidden)
    if (!hidden) edgeCount += 1
  })
  return { graph: createVisibleGraph(graph), nodeCount, edgeCount }
}

export const nodeView = (graph, id) => {
  if (!graph?.hasNode(id)) return null
  const attrs = graph.getNodeAttributes(id)
  return { id, label: attrs.label, kind: attrs.kind, color: attrs.baseColor, file: attrs.file, line: attrs.line }
}

export const edgeView = (graph, id) => {
  if (!graph?.hasEdge(id)) return null
  const attrs = graph.getEdgeAttributes(id)
  const [sourceId, targetId] = graph.extremities(id)
  return {
    id,
    relation: attrs.relation,
    relationLabel: edgeStyle(attrs.relation).label,
    dispatch: attrs.dispatch,
    color: attrs.baseColor,
    source: nodeView(graph, sourceId),
    target: nodeView(graph, targetId),
  }
}

export const relationRows = (graph, edgeIds, endpointPicker) => edgeIds
  .filter((edgeId) => !graph.getEdgeAttribute(edgeId, 'hidden'))
  .map((edgeId) => {
    const endpoint = endpointPicker(graph.extremities(edgeId))
    const attrs = graph.getEdgeAttributes(edgeId)
    return {
      id: endpoint,
      label: graph.getNodeAttribute(endpoint, 'label'),
      rel: edgeStyle(attrs.relation).label,
      color: attrs.baseColor,
    }
  })
