import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { createRenderer, nextTick, ref } from 'vue'
import { apiClient } from '../src/services/httpClient.js'
import { useSecurityPaths } from '../src/features/project-insight/composables/useSecurityPaths.js'
import { pathFamilies, securityGraph } from '../src/features/project-insight/domain/securityPaths.js'
import { pathFamilyColor, SHARED_PATH_COLOR, UNPAIRED_OPERATION_COLOR } from '../src/features/dependency-graph/graphStyle.js'
import { edgeView, relationRows } from '../src/features/dependency-graph/domain/graphModel.js'
import { useGraphModel } from '../src/features/dependency-graph/composables/useGraphModel.js'

const payload = () => ({ available: true, completed: true, paths: [
  { id: 'p1', node_ids: ['a', 'b'], edge_ids: ['call'], source: { symbol: 'a' }, sink: { symbol: 'b' } },
  { id: 'p2', node_ids: ['a', 'b', 'c'], edge_ids: ['instantiate'], source: { symbol: 'a' }, sink: { symbol: 'c' } },
], graph: {
  nodes: ['a', 'b', 'c'].map((id) => ({ id, name: id, level: id === 'c' ? 'external' : 'function' })),
  edges: [{ id: 'call', source: 'a', target: 'b', relation: 'calls' },
    { id: 'instantiate', source: 'a', target: 'b', relation: 'instantiates' }],
} })
const response = (config, data) => ({ config, status: 200, headers: {}, data: { code: 200, data } })

test('path membership is explicit, all nodes are visible, sizes uniform, parallel relations retain IDs', () => {
  const data = payload()
  const one = securityGraph(data, 'p1')
  assert.deepEqual(one.edges.map((edge) => edge.id), ['call']) // b also belongs to p2, but its other edge is not in p1.
  assert.deepEqual(one.nodes.map((node) => node.id), ['a', 'b'])
  const model = useGraphModel()
  model.build(securityGraph(data))
  model.applyVisibility()
  assert.equal(model.summary.visibleNodeCount, 3) // Isolated/external nodes are not hidden by the old core preset.
  assert.equal(model.summary.visibleEdgeCount, 2)
  assert.equal(model.graph.value.multi, true)
  assert.equal(new Set(model.graph.value.mapNodes((_id, attrs) => attrs.size)).size, 1)
  assert.equal(edgeView(model.graph.value, 'instantiate').relation, 'instantiates')
  assert.equal(model.node('a').security.pathCount, 2)
  const rows = relationRows(model.graph.value, model.graph.value.outEdges('a'), ([, target]) => target)
  assert.equal(new Set(rows.map((row) => row.edgeId)).size, 2)
  model.activeRelations.delete('instantiates')
  model.applyVisibility()
  assert.equal(model.summary.visibleEdgeCount, 1)
})

test('family colors follow group IDs, remain stable on route selection/reordering, and do not mutate saved facts', () => {
  const data = payload()
  data.paths[0].path_group_id = 'group-a'
  data.paths[1].path_group_id = 'group-b'
  data.paths.push({ ...data.paths[0], id: 'p3' })
  const original = JSON.stringify(data)
  const all = securityGraph(data)
  assert.equal(all.pathFamilies.length, 2)
  assert.deepEqual(all.pathFamilies.map((family) => family.routeCount), [2, 1])
  const first = securityGraph(data, 'p1')
  const sameGroup = securityGraph(data, 'p3')
  const other = securityGraph(data, 'p2')
  assert.equal(first.edges[0].security.color, sameGroup.edges[0].security.color)
  assert.notEqual(first.edges[0].security.color, other.edges[0].security.color)
  assert.deepEqual(first.pathFamilies[0], all.pathFamilies[0])
  assert.deepEqual(other.pathFamilies[0], all.pathFamilies[1])
  assert.deepEqual(pathFamilies([...data.paths].reverse()), all.pathFamilies)
  assert.equal(first.nodes[0].security.color, first.edges[0].security.color)
  assert.equal(JSON.stringify(data), original)
  // Compatibility: an ungrouped route is not silently merged into another route's family.
  assert.equal(pathFamilies(payload().paths).length, 2)
})

test('cross-family edges remain single neutral edges with every explicit membership and direction retained', () => {
  const data = payload()
  data.paths[0].path_group_id = 'group-a'
  data.paths[1].path_group_id = 'group-b'
  data.paths[1].edge_ids.push('call')
  const all = securityGraph(data)
  assert.equal(all.edges.length, data.graph.edges.length)
  assert.equal(all.hasSharedFamilies, true)
  const shared = all.edges.find((edge) => edge.id === 'call')
  assert.equal(shared.security.color, SHARED_PATH_COLOR)
  assert.equal(shared.security.pathCount, 2)
  assert.deepEqual(shared.security.families.map((family) => family.id), ['group-a', 'group-b'])
  assert.equal(shared.source, 'a')
  assert.equal(shared.target, 'b')
  assert.equal(all.nodes[0].security.color, SHARED_PATH_COLOR)
  const selected = securityGraph(data, 'p2')
  assert.equal(selected.hasSharedFamilies, false)
  assert.equal(selected.edges.find((edge) => edge.id === 'call').security.color, all.pathFamilies[1].color)
  const model = useGraphModel()
  model.build(all)
  assert.equal(edgeView(model.graph.value, 'call').color, SHARED_PATH_COLOR)
  assert.deepEqual(edgeView(model.graph.value, 'call').security, shared.security)
  assert.equal(edgeView(model.graph.value, 'instantiate').color, all.pathFamilies[1].color)
  assert.equal(new Set(model.graph.value.mapNodes((_id, attrs) => attrs.size)).size, 1)
})

test('multiple routes within one family are not cross-family shared; repeated IDs do not inflate membership counts', () => {
  const data = payload()
  for (const route of data.paths) route.path_group_id = 'one-family'
  data.paths[1].edge_ids.push('call', 'call')
  data.paths[0].node_ids.push('a')
  const graph = securityGraph(data)
  assert.equal(graph.hasSharedFamilies, false)
  assert.equal(graph.edges[0].security.pathCount, 2)
  assert.equal(graph.nodes[0].security.pathCount, 2)
  assert.equal(graph.edges[0].security.color, graph.pathFamilies[0].color)
})

test('family palette extends beyond preset colors and the renderer registers target arrowheads', async () => {
  const colors = Array.from({ length: 500 }, (_, index) => pathFamilyColor(index))
  assert.equal(new Set(colors).size, colors.length)
  assert.ok(colors.every((color) => /^#[a-f\d]{6}$/i.test(color))) // Focus fading accepts hex colors.
  const renderer = await readFile(new URL('../src/features/dependency-graph/composables/useSigmaRenderer.js', import.meta.url), 'utf8')
  assert.match(renderer, /createEdgeCurveProgram.*from '@sigma\/edge-curve'/)
  assert.match(renderer, /extremity: 'target'/)
  assert.match(renderer, /edgeProgramClasses: \{ curved: DirectedCurveProgram \}/)
})

const operationsPayload = () => ({
  ...payload(),
  operations: [
    { id: 'bash-run', kind: 'unpaired_operation', node_id: 'bash', sink: { name: 'subprocess.run', symbol: 'bash', location: { path: 'providers/bash.py', line: 36 } } },
    { id: 'bash-popen', kind: 'unpaired_operation', node_id: 'bash', sink: { name: 'subprocess.Popen', symbol: 'bash', location: { path: 'providers/bash.py', line: 78 } } },
    { id: 'python-eval', kind: 'unpaired_operation', node_id: 'python', sink: { name: 'eval', symbol: 'python', location: { path: 'providers/python.py', line: 52 } } },
  ],
  graph: { nodes: [...payload().graph.nodes,
    { id: 'bash', name: 'BashProvider._query', level: 'method' },
    { id: 'python', name: 'PythonProvider._query', level: 'method' }], edges: payload().graph.edges },
})

test('unpaired operations are visible without fabricated families, sources or edges and can be selected independently', () => {
  const data = operationsPayload()
  const before = JSON.stringify(data)
  const all = securityGraph(data)
  assert.equal(all.operationCount, 3)
  assert.equal(all.pathFamilies.length, 2)
  assert.equal(all.edges.length, data.graph.edges.length)
  const bash = all.nodes.find((node) => node.id === 'bash')
  assert.equal(bash.security.pathCount, 0)
  assert.equal(bash.security.color, UNPAIRED_OPERATION_COLOR)
  assert.deepEqual(bash.security.roles, ['Sink'])
  assert.deepEqual(bash.security.families, [])
  assert.equal(bash.security.operations.length, 2)
  const one = securityGraph(data, 'python-eval')
  assert.deepEqual(one.nodes.map((node) => node.id), ['python'])
  assert.deepEqual(one.edges, [])
  assert.deepEqual(one.pathFamilies, [])
  assert.equal(one.operationCount, 1)
  assert.equal(one.nodes[0].security.operations[0].id, 'python-eval')
  assert.deepEqual(securityGraph(data, 'p1').nodes.map((node) => node.id), ['a', 'b'])
  assert.equal(securityGraph(data, 'p1').operationCount, 0)
  const model = useGraphModel()
  model.build(one)
  model.applyVisibility()
  assert.equal(model.summary.visibleNodeCount, 1) // The old degree filter must not hide an isolated operation.
  assert.equal(model.summary.visibleEdgeCount, 0)
  assert.equal(model.node('python').label, 'PythonProvider._query')
  assert.equal(JSON.stringify(data), before)
})

test('an unpaired operation on a path member retains its family style but cannot expand that route', () => {
  const data = operationsPayload()
  data.operations[0].node_id = 'a'
  const all = securityGraph(data)
  const shared = all.nodes.find((node) => node.id === 'a')
  assert.equal(shared.security.color, SHARED_PATH_COLOR)
  assert.equal(shared.security.pathCount, 2)
  assert.equal(shared.security.operations.length, 1)
  const one = securityGraph(data, 'bash-run')
  assert.deepEqual(one.nodes.map((node) => node.id), ['a'])
  assert.deepEqual(one.edges, [])
  assert.deepEqual(one.pathFamilies, [])
})

test('Explore, details and graph inspector templates compile without raw HTML injection', async () => {
  for (const file of ['features/project-insight/views/ProjectExploreWorkspace.vue',
    'features/project-insight/components/SecurityPathDetails.vue', 'features/dependency-graph/components/GraphInspector.vue',
    'features/dependency-graph/DependencyGraph.vue', 'features/dependency-graph/components/GraphControls.vue']) {
    const source = await readFile(new URL('../src/' + file, import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    const script = compileScript(descriptor, { id: 'security-paths' })
    assert.deepEqual(compileTemplate({ id: 'security-paths', source: descriptor.template.content,
      compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
    assert.doesNotMatch(source, /v-html/)
  }
})

const mountPaths = () => {
  let paths
  const project = ref('first')
  const renderer = createRenderer({ createComment: () => ({}), insert: () => {}, remove: () => {},
    parentNode: () => null, nextSibling: () => null })
  const app = renderer.createApp({ setup() { paths = useSecurityPaths(project); return () => null } })
  app.mount({})
  return { paths, project, unmount: () => app.unmount() }
}
const flush = async () => { await new Promise((resolve) => setImmediate(resolve)); await nextTick() }

test('the read-only selector loads operation details through the same cancellable API without requiring a Source', async () => {
  const original = apiClient.defaults.adapter
  apiClient.defaults.adapter = async (config) => response(config,
    config.url.endsWith('/python-eval') ? { id: 'python-eval', steps: [] } : operationsPayload())
  const { paths, unmount } = mountPaths()
  try {
    await flush()
    paths.selectedId.value = 'python-eval'
    await flush()
    assert.equal(paths.selected.value.kind, 'unpaired_operation')
    assert.equal(paths.selected.value.source, undefined)
    assert.equal(paths.detail.value.id, 'python-eval')
    assert.equal(paths.graphData.value.nodes.length, 1)
    assert.equal(paths.graphData.value.edges.length, 0)
  } finally { unmount(); apiClient.defaults.adapter = original }
})

test('late project responses and details never overwrite a newer project or selection', async () => {
  const original = apiClient.defaults.adapter
  const pending = []
  apiClient.defaults.adapter = (config) => new Promise((resolve) => { pending.push({ config, resolve }) })
  const { paths, project, unmount } = mountPaths()
  try {
    project.value = 'second'
    await nextTick()
    assert.equal(pending[0].config.signal.aborted, true)
    pending[1].resolve(response(pending[1].config, payload()))
    await flush()
    paths.selectedId.value = 'p1'
    await nextTick()
    paths.selectedId.value = 'p2'
    await nextTick()
    assert.equal(pending[2].config.signal.aborted, true)
    pending[3].resolve(response(pending[3].config, { id: 'p2', steps: [] }))
    pending[2].resolve(response(pending[2].config, { id: 'p1', steps: [] }))
    pending[0].resolve(response(pending[0].config, { ...payload(), paths: [] }))
    await flush()
    assert.equal(paths.detail.value.id, 'p2')
    assert.equal(paths.data.value.paths.length, 2)
    const refresh = paths.reload()
    unmount()
    assert.equal(pending[4].config.signal.aborted, true)
    pending[4].resolve(response(pending[4].config, payload()))
    await refresh
    assert.equal(paths.data.value, null)
  } finally { unmount(); apiClient.defaults.adapter = original }
})

test('failed overview and detail can be retried without falling back to the full dependency graph', async () => {
  const original = apiClient.defaults.adapter
  let failOverview = true
  let failDetail = true
  apiClient.defaults.adapter = async (config) => {
    const detail = config.url.endsWith('/p1')
    if (detail ? failDetail : failOverview) throw new Error('暂时不可读')
    return response(config, detail ? { id: 'p1', steps: [] } : payload())
  }
  const { paths, project, unmount } = mountPaths()
  try {
    await flush()
    assert.equal(paths.error.value, '暂时不可读')
    assert.equal(paths.graphData.value.nodes.length, 0)
    failOverview = false
    await paths.reload()
    paths.selectedId.value = 'p1'
    await flush()
    assert.equal(paths.detailError.value, '暂时不可读')
    failDetail = false
    await paths.loadDetail()
    assert.equal(paths.detail.value.id, 'p1')
    project.value = ''
    await flush()
    assert.equal(paths.loading.value, false)
    assert.equal(paths.selectedId.value, '')
    assert.equal(paths.data.value, null)
  } finally { unmount(); apiClient.defaults.adapter = original }
})
