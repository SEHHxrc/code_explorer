import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSymbolTree } from '../src/features/project-insight/domain/symbolTree.js'

test('buildSymbolTree nests members and tolerates missing extents', () => {
  const tree = buildSymbolTree([
    { name: 'App', kind: 'class', fully_qualified_name: 'App', extent_utf16: { start: { line_number: 2 } } },
    { name: 'run', kind: 'method', fully_qualified_name: 'App.run' },
    { name: 'main', kind: 'function', fully_qualified_name: 'main', line: 8 },
  ])
  assert.equal(tree[0].name, 'App')
  assert.equal(tree[0].children[0].name, 'run')
  assert.equal(tree[0].children[0].line, null)
  assert.equal(tree[1].name, '函数')
  assert.equal(tree[1].children[0].line, 8)
})