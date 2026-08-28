/**
 * Parse a JSON argv array. A shell command string is deliberately rejected because the backend
 * launches containers with shell=false.
 */
export const parseArgv = (text) => {
  let value
  try {
    value = JSON.parse(text)
  } catch (_) {
    throw new Error('命令参数必须是合法 JSON 数组')
  }
  if (!Array.isArray(value) || !value.length || value.length > 64) {
    throw new Error('命令参数必须包含 1 至 64 个数组元素')
  }
  if (value.some((item) => typeof item !== 'string' || !item || item.includes('\0'))) {
    throw new Error('每个命令参数都必须是非空字符串')
  }
  return value
}
