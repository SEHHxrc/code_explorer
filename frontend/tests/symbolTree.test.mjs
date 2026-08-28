import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSymbolTree } from '../src/features/project-insight/domain/symbolTree.js'
import { resolveSymbolNodeId } from '../src/features/dependency-graph/domain/graphModel.js'

test('buildSymbolTree nests members and tolerates missing extents', () => {
  const tree = buildSymbolTree([
    { name: 'App', kind: 'class', fully_qualified_name: 'App', extent_utf16: { start: { line_number: 2 } } },
    { name: 'run', kind: 'method', fully_qualified_name: 'App.run' },
    { name: 'main', kind: 'function', fully_qualified_name: 'main', line: 8 },
  ])
  assert.equal(tree[0].name, 'App')
  assert.equal(tree[0].children[0].name, 'run')
  assert.equal(tree[0].children[0].line, null)
  assert.equal(tree[0].children[0].fqn, 'App.run')
  assert.equal(tree[1].name, '函数')
  assert.equal(tree[1].children[0].line, 8)
})
test('resolveSymbolNodeId prefers canonical id and safely falls back to its file', () => {
  const nodes = [
    { id: 'src/main.py', name: 'main.py', level: 'module', file: 'src/main.py' },
    { id: 'src/main.py::App::run', name: 'run', level: 'method', file: 'src/main.py', line: 8 },
    { id: 'src/other.py::run', name: 'run', level: 'function', file: 'src/other.py', line: 8 },
  ]
  assert.equal(
    resolveSymbolNodeId(nodes, { file: 'src/main.py', name: 'run', fqn: 'App.run', line: 8 }),
    'src/main.py::App::run',
  )
  assert.equal(
    resolveSymbolNodeId(nodes, { file: 'src/main.py', name: 'missing', fqn: 'missing' }),
    'src/main.py',
  )
})
