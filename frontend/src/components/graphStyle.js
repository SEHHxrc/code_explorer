/**
 * 依赖图视觉规则（浅色主题）。节点类别使用高区分度色相与标签区分，
 * 所有节点保持统一尺寸，类别与层次只通过颜色和标签表达。
 */

const KIND_COLORS = {
  module: '#2f7de1',
  namespace: '#7c4dff',
  class: '#e8930c',
  struct: '#e8930c',
  record: '#e8930c',
  interface: '#e0439a',
  trait: '#e0439a',
  enum: '#ef7014',
  union: '#ef7014',
  type: '#9575e8',
  function: '#0ea472',
  constructor: '#0ea472',
  method: '#0da5a0',
  field: '#8492a6',
  property: '#8492a6',
  variable: '#8492a6',
  constant: '#8492a6',
  macro: '#c99a06',
  builtin: '#6366f1',
  stdlib: '#64748b',
  stdlib_module: '#475569',
  external: '#94a3b8',
  external_module: '#7c8797',
}

const LEVEL_COLORS = {
  module: '#2f7de1',
  class: '#e8930c',
  function: '#0ea472',
  method: '#0da5a0',
  property: '#8492a6',
  variable: '#8492a6',
  builtin: '#6366f1',
  stdlib: '#64748b',
  stdlib_module: '#475569',
  external: '#94a3b8',
  external_module: '#7c8797',
}

const FALLBACK_COLOR = '#98a2ad'

/**
 * ForceAtlas2 的节点质量来自加权度数。包含边权重过高会把成员吸回文件节点，
 * 因此骨架边保持低权重；参数依据 layout-bench.mjs 的真实图谱测试。
 */
const EDGE_WEIGHTS = {
  contains: 0.1,
  declares: 0.5,
  inherits: 1.2,
  implements: 1.2,
  embeds: 1.2,
  imports: 0.72,
  instantiates: 0.5,
  calls: 0.45,
  overrides: 0.36,
  uses: 0.22,
}

/** 输入关系类型，输出 ForceAtlas2 使用的边权重。 */
export const edgeWeight = (relation) => EDGE_WEIGHTS[relation] ?? 0.45

export const NODE_LEVELS = [
  { key: 'module', label: '文件 / 模块', color: LEVEL_COLORS.module },
  { key: 'class', label: '类 / 结构体 / 接口', color: LEVEL_COLORS.class },
  { key: 'function', label: '函数', color: LEVEL_COLORS.function },
  { key: 'method', label: '方法', color: LEVEL_COLORS.method },
  { key: 'variable', label: '全局变量 / 常量', color: LEVEL_COLORS.variable },
  { key: 'property', label: '字段 / 属性', color: LEVEL_COLORS.property },
  { key: 'builtin', label: '语言内置', color: LEVEL_COLORS.builtin },
  { key: 'stdlib', label: '语言标准库', color: LEVEL_COLORS.stdlib },
  { key: 'stdlib_module', label: '标准库模块', color: LEVEL_COLORS.stdlib_module },
  { key: 'external_module', label: '第三方包', color: LEVEL_COLORS.external_module },
  { key: 'external', label: '第三方符号', color: LEVEL_COLORS.external },
]

export const LEVEL_ALIASES = {}

/** 输入节点属性，输出优先按符号类型、其次按层级选择的颜色。 */
export const nodeColor = (node) =>
  KIND_COLORS[node.kind] || LEVEL_COLORS[node.level] || FALLBACK_COLOR

/** 同一张图内所有节点严格等大；只随总规模统一缩放。 */
export const nodeSize = (_node, nodeCount) => {
  if (nodeCount > 20000) return 2.6
  if (nodeCount > 5000) return 3.1
  if (nodeCount > 1500) return 3.7
  return 4.5
}

export const EDGE_STYLES = {
  contains: { color: '#d8dde4', width: 0.35, label: '包含' },
  declares: { color: '#dce1e7', width: 0.3, label: '声明于' },
  imports: { color: '#91afd0', width: 0.7, label: '导入' },
  calls: { color: '#aaa1c2', width: 0.9, label: '调用' },
  instantiates: { color: '#83adb8', width: 0.75, label: '实例化' },
  inherits: { color: '#c09a7d', width: 1.2, label: '继承' },
  implements: { color: '#bd91a5', width: 1.1, label: '实现' },
  embeds: { color: '#bca777', width: 1.1, label: '嵌入' },
  overrides: { color: '#aa95bd', width: 0.9, label: '重写' },
  uses: { color: '#e1e4e9', width: 0.3, label: '使用类型' },
}

const DEFAULT_EDGE_STYLE = { color: '#d9dde3', width: 0.4, label: '关系' }

/** 输入关系类型，输出边颜色、宽度和中文标签。 */
export const edgeStyle = (relation) => EDGE_STYLES[relation] || DEFAULT_EDGE_STYLE

export const DYNAMIC_CALL_COLOR = '#b6aed0'

export const EDGE_RELATIONS = Object.entries(EDGE_STYLES).map(([key, style]) => ({
  key,
  label: style.label,
  color: style.color,
}))

const CANVAS_RGB = [248, 250, 252]

/** 将非焦点颜色向画布背景融合，而不是在浅色主题下压成抢眼的深色。 */
export const fadeColor = (hex, amount) => {
  const value = String(hex || FALLBACK_COLOR).replace('#', '')
  if (value.length !== 6) return hex
  const mix = (channel, bg) => Math.round(channel + (bg - channel) * amount)
  const r = mix(parseInt(value.slice(0, 2), 16), CANVAS_RGB[0])
  const g = mix(parseInt(value.slice(2, 4), 16), CANVAS_RGB[1])
  const b = mix(parseInt(value.slice(4, 6), 16), CANVAS_RGB[2])
  return `rgb(${r},${g},${b})`
}

export const CANVAS_BG = '#f8fafc'
