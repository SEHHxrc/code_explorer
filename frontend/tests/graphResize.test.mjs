import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { observeGraphSize } from '../src/features/dependency-graph/composables/observeGraphSize.js'

const observerHarness = (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const previous = globalThis.ResizeObserver
  let observer
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.disconnected = false; observer = this }
    observe(element) { this.element = element }
    disconnect() { this.disconnected = true }
  }
  t.after(() => {
    if (previous === undefined) delete globalThis.ResizeObserver
    else globalThis.ResizeObserver = previous
  })
  const element = {}
  let updates = 0
  const stop = observeGraphSize(element, () => { updates++ })
  return { stop, get observer() { return observer }, get updates() { return updates },
    notify: (width, height, target = element) => observer.callback([{ target, contentRect: { width, height } }]),
  }
}

test('continuous container changes are coalesced and identical sizes do not postpone the final resize', (t) => {
  const harness = observerHarness(t)
  for (let width = 400; width < 500; width++) harness.notify(width, 600)
  t.mock.timers.tick(100)
  assert.equal(harness.updates, 0)
  harness.notify(499, 600)
  t.mock.timers.tick(20)
  assert.equal(harness.updates, 1)
  harness.notify(499, 600)
  t.mock.timers.tick(200)
  assert.equal(harness.updates, 1)
  harness.notify(500, 600)
  t.mock.timers.tick(120)
  assert.equal(harness.updates, 2)
  harness.stop()
})

test('a zero-sized or hidden container cancels pending updates and is updated after becoming visible', (t) => {
  const harness = observerHarness(t)
  harness.notify(800, 500)
  harness.notify(0, 0)
  t.mock.timers.tick(300)
  assert.equal(harness.updates, 0)
  harness.notify(800, 500)
  t.mock.timers.tick(120)
  assert.equal(harness.updates, 1)
  harness.stop()
})

test('disconnect cancels pending work and late ResizeObserver callbacks cannot revive it', (t) => {
  const harness = observerHarness(t)
  harness.notify(800, 500)
  harness.stop()
  harness.notify(900, 600)
  t.mock.timers.tick(300)
  assert.equal(harness.updates, 0)
  assert.equal(harness.observer.disconnected, true)
})

test('irrelevant observer entries and rounded duplicate sizes do not refresh', (t) => {
  const harness = observerHarness(t)
  harness.notify(800, 500, {})
  t.mock.timers.tick(120)
  assert.equal(harness.updates, 0)
  harness.notify(800.1, 500.1)
  t.mock.timers.tick(120)
  harness.notify(800.2, 500.2)
  t.mock.timers.tick(120)
  assert.equal(harness.updates, 1)
  harness.stop()
})

test('a missing container or unsupported observer is safe during SSR and unmount', () => {
  assert.doesNotThrow(() => observeGraphSize(null, () => assert.fail())())
  assert.doesNotThrow(() => observeGraphSize({}, () => assert.fail())())
})

test('static paths hide graph presets; resize uses cache and inactive graphs release Sigma listeners', async () => {
  const base = new URL('../src/features/dependency-graph/', import.meta.url)
  const component = await readFile(new URL('DependencyGraph.vue', base), 'utf8')
  const renderer = await readFile(new URL('composables/useSigmaRenderer.js', base), 'utf8')
  const controls = await readFile(new URL('components/GraphControls.vue', base), 'utf8')
  assert.match(component, /:presets="graphData\.pathFamilies \? \[\] : VIEW_PRESETS"/)
  assert.match(controls, /v-if="presets\.length" class="preset-group"/)
  assert.match(component, /:path-mode="Boolean\(graphData\.pathFamilies\)"/)
  assert.equal((controls.match(/el-popover v-if="!pathMode"/g) || []).length, 2) // Relation and degree filters.
  assert.doesNotMatch(component, /window\.addEventListener\(['"]resize/)
  assert.match(renderer, /sigma\.scheduleRefresh\(\{ partialGraph: \{ nodes: \[\], edges: \[\] \} \}\)/)
  assert.match(component, /sigma\.destroy\(\{ preserveCamera: true \}\)/)
  assert.match(renderer, /if \(camera\) sigma\.getCamera\(\)\.setState\(camera\)/)
})
