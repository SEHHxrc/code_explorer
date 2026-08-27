import { reactive, ref, shallowRef } from 'vue'
import { EDGE_RELATIONS, NODE_LEVELS } from '../graphStyle.js'
import {
  createGraph,
  edgeView,
  filterGraph,
  nodeView,
  relationRows,
} from '../domain/graphModel.js'

export const VIEW_PRESETS = [
  { key: 'core', label: '核心' },
  { key: 'architecture', label: '架构' },
  { key: 'calls', label: '调用' },
  { key: 'all', label: '全部' },
]

const presetRules = {
  core: {
    levels: ['module', 'class', 'function', 'method', 'external_module'],
    relations: ['contains', 'declares', 'imports', 'calls', 'instantiates', 'inherits', 'implements', 'embeds', 'overrides'],
    degree: (count) => (count > 200 ? 2 : 0),
  },
  architecture: {
    levels: ['module', 'class', 'external_module'],
    relations: ['contains', 'declares', 'imports', 'inherits', 'implements', 'embeds'],
    degree: () => 0,
  },
  calls: {
    levels: ['class', 'function', 'method', 'external_module', 'external'],
    relations: ['calls', 'instantiates', 'overrides'],
    degree: (count) => (count > 200 ? 1 : 0),
  },
  all: {
    levels: NODE_LEVELS.map((item) => item.key),
    relations: EDGE_RELATIONS.map((item) => item.key),
    degree: () => 0,
  },
}

/** Own the canonical Graphology graph, filters, summaries, and visible subgraph. */
export const useGraphModel = () => {
  const graph = shallowRef(null)
  const displayGraph = shallowRef(null)
  const summary = reactive({
    nodeCount: 0,
    edgeCount: 0,
    visibleNodeCount: 0,
    visibleEdgeCount: 0,
    levelCounts: {},
    relationCounts: {},
    maxDegree: 0,
  })
  const activeLevels = reactive(new Set())
  const activeRelations = reactive(new Set())
  const manuallyHidden = reactive(new Set())
  const minDegree = ref(0)
  const viewPreset = ref('core')

  const configurePreset = (key) => {
    const rule = presetRules[key] || presetRules.core
    activeLevels.clear()
    activeRelations.clear()
    rule.levels.forEach((item) => activeLevels.add(item))
    rule.relations.forEach((item) => activeRelations.add(item))
    minDegree.value = rule.degree(summary.nodeCount)
    viewPreset.value = key
  }

  const build = (payload) => {
    const built = createGraph(payload)
    graph.value = built.graph
    Object.assign(summary, built.summary, { visibleNodeCount: 0, visibleEdgeCount: 0 })
    manuallyHidden.clear()
    configurePreset('core')
    return graph.value
  }

  const applyVisibility = ({ selectedId = null, focusMode = false } = {}) => {
    if (!graph.value) return null
    const result = filterGraph(graph.value, {
      levels: activeLevels,
      relations: activeRelations,
      minDegree: minDegree.value,
      manuallyHidden,
      selectedId,
      focusMode,
    })
    displayGraph.value = result.graph
    summary.visibleNodeCount = result.nodeCount
    summary.visibleEdgeCount = result.edgeCount
    return displayGraph.value
  }

  const node = (id) => nodeView(graph.value, id)
  const edge = (id) => edgeView(graph.value, id)
  const relationsForNode = (id) => {
    if (!graph.value?.hasNode(id)) return { outgoing: [], incoming: [] }
    return {
      outgoing: relationRows(graph.value, graph.value.outEdges(id), ([, target]) => target),
      incoming: relationRows(graph.value, graph.value.inEdges(id), ([source]) => source),
    }
  }
  const search = (keyword, limit = 300) => {
    const hits = []
    const term = keyword.trim().toLowerCase()
    if (!graph.value || !term) return hits
    graph.value.forEachNode((id, attrs) => {
      if (attrs.hidden || hits.length >= limit) return
      if (attrs.label.toLowerCase().includes(term) || id.toLowerCase().includes(term)) hits.push(id)
    })
    return hits
  }
  const hideNode = (id) => { if (id) manuallyHidden.add(id) }
  const restoreHidden = () => manuallyHidden.clear()
  const destroy = () => {
    graph.value = null
    displayGraph.value = null
  }

  return {
    graph, displayGraph, summary,
    activeLevels, activeRelations, manuallyHidden, minDegree, viewPreset,
    build, destroy, configurePreset, applyVisibility,
    node, edge, relationsForNode, search, hideNode, restoreHidden,
  }
}
