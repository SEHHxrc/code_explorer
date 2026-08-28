import test from 'node:test'
import assert from 'node:assert/strict'
import { parseArgv } from '../src/features/execution/domain/parseArgv.js'

test('parseArgv accepts only non-empty JSON string arrays', () => {
  assert.deepEqual(parseArgv('["python", "-V"]'), ["python", "-V"])
  assert.throws(() => parseArgv('python -V'), /JSON/)
  assert.throws(() => parseArgv('[]'), /1 至 64/)
  assert.throws(() => parseArgv('["ok", 3]'), /非空字符串/)
})
