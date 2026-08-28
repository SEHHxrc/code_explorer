import { ref, shallowRef } from 'vue'
import FA2Layout from 'graphology-layout-forceatlas2/worker.js'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import noverlap from 'graphology-layout-noverlap'
import { collectParents, seedPositions } from '../domain/graphModel.js'

const settingsFor = (count) => {
  const small = count < 500
  const medium = count < 2000
  const large = count < 10000
  return {
    gravity: small ? 0.32 : medium ? 0.22 : large ? 0.14 : 0.08,
    scalingRatio: small ? 28 : medium ? 52 : large ? 86 : 130,
    slowDown: small ? 1 : medium ? 2 : large ? 3 : 5,
    barnesHutOptimize: count > 200,
    barnesHutTheta: large ? 0.6 : 0.85,
    strongGravityMode: false,
    outboundAttractionDistribution: true,
    linLogMode: true,
    adjustSizes: true,
    edgeWeightInfluence: 1,
  }
}

const durationFor = (count) => {
  if (count > 10000) return 9000
  if (count > 5000) return 7000
  if (count > 1000) return 4500
  if (count > 300) return 2800
  return 1800
}

/** Own the ForceAtlas2 worker, stop timer, coordinate synchronization, and overlap pass. */
export const useGraphLayout = ({ fullGraph, displayGraph, renderer, renderState, onSettled }) => {
  const worker = shallowRef(null)
  const running = ref(false)
  let stopTimer = null
  let generation = 0

  const sync = (source = displayGraph.value) => {
    if (!fullGraph.value || !source) return
    source.forEachNode((id, attrs) => {
      if (fullGraph.value.hasNode(id)) fullGraph.value.mergeNodeAttributes(id, { x: attrs.x, y: attrs.y })
    })
    renderer.value?.refresh()
  }

  const settle = () => {
    const target = displayGraph.value
    if (!target || target.order < 2 || target.order > 3000) return
    noverlap.assign(target, {
      maxIterations: target.order > 1500 ? 55 : 110,
      settings: { ratio: 1.6, margin: 8, expansion: 1.16, gridSize: 24, speed: 3 },
    })
    sync(target)
  }

  const stop = () => {
    generation += 1
    if (stopTimer) { clearTimeout(stopTimer); stopTimer = null }
    if (worker.value) { worker.value.kill(); worker.value = null }
    sync()
    running.value = false
    renderState.layoutActive = false
    renderer.value?.refresh()
  }

  /**
   * Tear down background layout work without touching Sigma.
   * During component unmount the container can already have zero width, so the normal stop/sync
   * path is unsafe here and would make Sigma throw before the project UI finishes resetting.
   */
  const destroy = () => {
    generation += 1
    if (stopTimer) { clearTimeout(stopTimer); stopTimer = null }
    if (worker.value) { worker.value.kill(); worker.value = null }
    running.value = false
    renderState.layoutActive = false
  }

  const start = () => {
    const target = displayGraph.value
    if (!target || target.order < 2) return
    stop()
    const currentGeneration = ++generation
    worker.value = new FA2Layout(target, {
      settings: { ...forceAtlas2.inferSettings(target), ...settingsFor(target.order) },
    })
    worker.value.start()
    running.value = true
    renderState.layoutActive = true
    renderer.value?.refresh()
    stopTimer = setTimeout(() => {
      if (currentGeneration !== generation) return
      stop()
      settle()
      onSettled?.()
    }, durationFor(target.order))
  }

  const restart = () => {
    const target = displayGraph.value
    if (!target) return
    stop()
    renderer.value?.setCustomBBox(null)
    seedPositions(target, collectParents(target))
    sync(target)
    fullGraph.value?.forEachNode((id) => fullGraph.value.setNodeAttribute(id, 'pinned', false))
    start()
  }

  const toggle = () => {
    if (running.value) { stop(); settle() } else start()
  }

  return { running, start, stop, restart, toggle, settle, sync, destroy }
}
