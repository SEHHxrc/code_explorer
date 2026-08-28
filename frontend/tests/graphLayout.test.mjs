import test from 'node:test'
import assert from 'node:assert/strict'
import { useGraphLayout } from '../src/features/dependency-graph/composables/useGraphLayout.js'

test('destroy never refreshes Sigma after its container starts unmounting', () => {
  let refreshCount = 0
  const renderState = { layoutActive: true }
  const layout = useGraphLayout({
    fullGraph: { value: null },
    displayGraph: { value: null },
    renderer: { value: { refresh: () => { refreshCount += 1 } } },
    renderState,
  })

  layout.destroy()

  assert.equal(refreshCount, 0)
  assert.equal(layout.running.value, false)
  assert.equal(renderState.layoutActive, false)
})
