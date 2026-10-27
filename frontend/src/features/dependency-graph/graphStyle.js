// Feature-local public entry. The compatibility module remains available to old callers.
export * from '../../components/graphStyle.js'

export const SHARED_PATH_COLOR = '#64748b'
export const UNPAIRED_OPERATION_COLOR = '#a16207'
export const PATH_EDGE_WIDTH = 1.6

const FAMILY_PALETTE = ['#2563eb', '#d97706', '#0d9488', '#9333ea', '#e11d48', '#0284c7', '#65a30d', '#c026d3']

/** 输入路径族序号，输出浅色画布上的稳定色；超出预设色板时按黄金角扩展，避免循环复用。 */
export const pathFamilyColor = (index) => {
  if (index < FAMILY_PALETTE.length) return FAMILY_PALETTE[index]
  const hue = (index * 137.508) % 360
  const channel = (offset) => {
    const k = (offset + hue / 30) % 12
    return Math.round(255 * (0.43 - 0.28 * Math.max(-1, Math.min(k - 3, 9 - k, 1))))
      .toString(16).padStart(2, '0')
  }
  return `#${channel(0)}${channel(8)}${channel(4)}`
}
