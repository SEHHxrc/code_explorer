/**
 * 力导向布局参数评测台 —— DependencyGraph.vue 里那几个「魔数」的来源。
 *
 * 指标：节点重叠对数、归一化最近邻距离、边交叉率、布局占地半径。
 * 坐标先按「平均边长 = 1」归一化，这样不同 scalingRatio 的结果才可比
 * （也正因如此可以看出：单纯放大 scalingRatio 对视觉密度毫无帮助）。
 *
 * 用法：
 *   1) 先用后端导出真实图谱到 %TEMP%：
 *      python -c "import sys,json,os; sys.path.insert(0,'.');  *        from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer;  *        r=UnifiedCodeAnalyzer('backend').run_full_analysis();  *        json.dump(r['dependency_graph'], open(os.environ['TEMP']+'/graph_mid.json','w'))"
 *   2) node layout-bench.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import noverlap from 'graphology-layout-noverlap'

const TMP = process.env.TEMP || 'C:/Users/SEHH/AppData/Local/Temp'

// --- 与前端 graphStyle.js 保持一致的尺寸/权重表 ---
const sizeOf = (_node, count) => {
  if (count > 20000) return 2.6
  if (count > 5000) return 3.1
  if (count > 1500) return 3.7
  return 4.5
}

const buildGraph = (file, weights) => {
  const data = JSON.parse(fs.readFileSync(file, 'utf8'))
  const g = new Graph({ type: 'directed', multi: false, allowSelfLoops: false })
  for (const n of data.nodes) {
    if (g.hasNode(n.id)) continue
    g.addNode(n.id, { kind: n.kind, level: n.level, x: 0, y: 0, size: 3 })
  }
  const parentOf = new Map()
  for (const e of data.links) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target) || e.source === e.target) continue
    if (g.hasEdge(e.source, e.target)) continue
    const rel = e.relation || 'calls'
    g.addDirectedEdge(e.source, e.target, { relation: rel, weight: weights[rel] ?? 0.5 })
    if ((rel === 'contains' || rel === 'declares') && !parentOf.has(e.target)) parentOf.set(e.target, e.source)
  }
  const count = g.order
  g.forEachNode((id, a) => g.setNodeAttribute(id, 'size', sizeOf(a, count)))
  return { g, parentOf }
}

const seed = (g, parentOf) => {
  const count = g.order
  const spread = Math.sqrt(count) * 45
  const golden = Math.PI * (3 - Math.sqrt(5))
  const roots = [], rest = []
  g.forEachNode((id) => (parentOf.has(id) ? rest.push(id) : roots.push(id)))
  roots.forEach((id, i) => {
    const r = spread * Math.sqrt((i + 1) / Math.max(roots.length, 1))
    g.setNodeAttribute(id, 'x', Math.cos(i * golden) * r)
    g.setNodeAttribute(id, 'y', Math.sin(i * golden) * r)
  })
  const jitter = Math.max(12, Math.sqrt(count) * 2.5)
  const placed = new Set(roots)
  let pending = rest, guard = 0
  while (pending.length && guard++ < 12) {
    const next = []
    for (const id of pending) {
      const p = parentOf.get(id)
      if (!p || !placed.has(p)) { next.push(id); continue }
      const a = Math.random() * Math.PI * 2
      const r = jitter * (0.4 + Math.random() * 0.8)
      g.setNodeAttribute(id, 'x', g.getNodeAttribute(p, 'x') + Math.cos(a) * r)
      g.setNodeAttribute(id, 'y', g.getNodeAttribute(p, 'y') + Math.sin(a) * r)
      placed.add(id)
    }
    if (next.length === pending.length) break
    pending = next
  }
  pending.forEach((id, i) => {
    g.setNodeAttribute(id, 'x', Math.cos(i * golden) * spread * 1.2)
    g.setNodeAttribute(id, 'y', Math.sin(i * golden) * spread * 1.2)
  })
}

// --- 指标 ---
const metrics = (g) => {
  const nodes = g.mapNodes((id, a) => ({ id, x: a.x, y: a.y, s: a.size }))
  // 归一化到统一尺度，让不同 scalingRatio 的结果可比：
  // 把坐标缩放成「平均边长 = 1」，再看重叠与最近邻距离
  const edges = g.mapEdges((e, a, s, t) => [s, t])
  const pos = new Map(nodes.map((n) => [n.id, n]))
  let sum = 0
  for (const [s, t] of edges) {
    const a = pos.get(s), b = pos.get(t)
    sum += Math.hypot(a.x - b.x, a.y - b.y)
  }
  const meanEdge = sum / Math.max(edges.length, 1)

  // 重叠：两节点圆心距 < 半径和（用渲染时的真实像素比例估算）
  // sigma 把图坐标映射到画布，节点 size 单位与坐标不同尺度，这里用
  // 「相对平均边长的节点半径」来判断视觉上是否糊在一起
  const scale = meanEdge > 0 ? 1 / meanEdge : 1
  let overlaps = 0
  let minDist = Infinity
  const cell = 1.0
  const grid = new Map()
  for (const n of nodes) {
    const gx = Math.floor(n.x * scale / cell), gy = Math.floor(n.y * scale / cell)
    const key = gx + ',' + gy
    if (!grid.has(key)) grid.set(key, [])
    grid.get(key).push(n)
  }
  const NODE_R = 0.06   // 节点半径 ≈ 平均边长的 6%（对应默认视图下的观感）
  for (const n of nodes) {
    const gx = Math.floor(n.x * scale / cell), gy = Math.floor(n.y * scale / cell)
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      for (const m of grid.get((gx + dx) + ',' + (gy + dy)) || []) {
        if (m.id <= n.id) continue
        const d = Math.hypot((n.x - m.x) * scale, (n.y - m.y) * scale)
        if (d < minDist) minDist = d
        if (d < NODE_R * (n.s + m.s) / 8) overlaps++
      }
    }
  }

  // 边交叉：节点数大时抽样
  const sample = edges.length > 1200 ? edges.filter(() => Math.random() < 1200 / edges.length) : edges
  let crossings = 0
  const ccw = (a, b, c) => (c.y - a.y) * (b.x - a.x) > (b.y - a.y) * (c.x - a.x)
  for (let i = 0; i < sample.length; i++) {
    const [s1, t1] = sample[i]
    const a = pos.get(s1), b = pos.get(t1)
    for (let j = i + 1; j < sample.length; j++) {
      const [s2, t2] = sample[j]
      if (s1 === s2 || s1 === t2 || t1 === s2 || t1 === t2) continue
      const c = pos.get(s2), d = pos.get(t2)
      if (ccw(a, c, d) !== ccw(b, c, d) && ccw(a, b, c) !== ccw(a, b, d)) crossings++
    }
  }
  const crossRate = crossings / Math.max(sample.length * (sample.length - 1) / 2, 1)

  let maxR = 0
  for (const n of nodes) maxR = Math.max(maxR, Math.hypot(n.x, n.y) * scale)
  return { overlaps, minDist, crossRate, maxR, meanEdge }
}

const run = (file, label, cfg) => {
  const { g, parentOf } = buildGraph(file, cfg.weights)
  seed(g, parentOf)
  const settings = { ...forceAtlas2.inferSettings(g), ...cfg.fa2 }
  forceAtlas2.assign(g, { iterations: cfg.iterations, settings })
  if (cfg.noverlap) noverlap.assign(g, cfg.noverlap)
  const m = metrics(g)
  console.log(
    '  ' + label.padEnd(22) +
    'overlaps=' + String(m.overlaps).padEnd(6) +
    'minDist=' + m.minDist.toFixed(3).padEnd(8) +
    'crossRate=' + m.crossRate.toFixed(5).padEnd(9) +
    'radius=' + m.maxR.toFixed(1),
  )
  return m
}

const W = (contains, structural, call) => ({
  contains, declares: contains * 0.85,
  inherits: structural, implements: structural, embeds: structural,
  imports: structural * 0.6,
  instantiates: call * 1.1, calls: call, overrides: call * 0.8, uses: call * 0.5,
})

const base = (n) => ({
  gravity: n < 500 ? 0.9 : 0.55,
  scalingRatio: n < 500 ? 14 : 30,
  slowDown: n < 500 ? 1 : 2,
  barnesHutOptimize: n > 200, barnesHutTheta: 0.85,
  strongGravityMode: false, outboundAttractionDistribution: true,
  linLogMode: false, adjustSizes: true, edgeWeightInfluence: 1,
})

// noverlap 的布局参数必须嵌在 settings 里，平铺写只有 maxIterations 生效
const NOV = (ratio, margin) => ({ maxIterations: 200, settings: { ratio, margin, expansion: 1.1, gridSize: 20, speed: 3 } })

const FA2 = (n) => ({ ...base(n), outboundAttractionDistribution: false })

// 线上采用的配置：contains 权重 0.6 + 关闭 outbound 吸引 + 默认参数的 noverlap
const CURRENT = (n) => ({
  weights: W(0.6, 1.2, 0.45),
  fa2: FA2(n),
  iterations: 1200,
  noverlap: NOV(1, 5),
})

// 改造前的配置，用于回归对比
const BASELINE = (n) => ({
  weights: W(3, 2, 0.5),
  fa2: { ...base(n), outboundAttractionDistribution: true },
  iterations: 1200,
})

for (const [file, n] of [[path.join(TMP, 'graph_small.json'), 57], [path.join(TMP, 'graph_mid.json'), 608]]) {
  console.log('--- ' + path.basename(file) + ' ---')
  run(file, '改造前', BASELINE(n))
  run(file, '线上配置', CURRENT(n))
}
