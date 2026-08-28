/** Convert flat analyzer symbols into a stable arbitrary-depth outline. */
export const buildSymbolTree = (symbols = []) => {
  const nodeByFqn = new Map()
  const roots = []
  const looseConstants = []
  const looseFunctions = []
  const makeNode = (symbol) => ({
    name: symbol?.name || '(anonymous)',
    type: symbol?.kind || 'unknown',
    fqn: String(symbol?.fully_qualified_name || symbol?.name || ''),
    line: Number(symbol?.extent_utf16?.start?.line_number || symbol?.line) || null,
    children: [],
  })
  const ordered = [...symbols].sort((left, right) => {
    const depth = (symbol) => String(symbol?.fully_qualified_name || symbol?.name || '').split('.').length
    return depth(left) - depth(right)
  })
  for (const symbol of ordered) {
    const fqn = String(symbol?.fully_qualified_name || symbol?.name || '')
    if (!fqn) continue
    const parts = fqn.split('.')
    const node = makeNode(symbol)
    nodeByFqn.set(fqn, node)
    if (parts.length === 1) {
      if (symbol.kind === 'class') roots.push(node)
      else if (['constant', 'property'].includes(symbol.kind)) looseConstants.push(node)
      else looseFunctions.push(node)
      continue
    }
    const parent = nodeByFqn.get(parts.slice(0, -1).join('.'))
    if (parent) parent.children.push(node)
    else if (['constant', 'property'].includes(symbol.kind)) looseConstants.push(node)
    else if (symbol.kind === 'class') roots.push(node)
    else looseFunctions.push(node)
  }
  const tree = []
  if (looseConstants.length) tree.push({ name: '常量 / 全局变量', type: 'category', children: looseConstants })
  tree.push(...roots)
  if (looseFunctions.length) tree.push({ name: '函数', type: 'category', children: looseFunctions })
  return tree
}
