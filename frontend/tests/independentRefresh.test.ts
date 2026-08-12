import assert from 'node:assert/strict'
import test from 'node:test'

import { applyIndependently } from '../src/renderer/src/api/independentRefresh'

function delayed<T>(value: T, milliseconds: number): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), milliseconds))
}

test('fast renderer state applies before a slow sibling request settles', async () => {
  const updates: string[] = []
  const fast = applyIndependently(delayed('stats', 10), (value) => updates.push(value))
  const slow = applyIndependently(delayed('dht', 250), (value) => updates.push(value))

  await delayed(undefined, 50)
  assert.deepEqual(updates, ['stats'])

  await Promise.allSettled([fast, slow])
  assert.deepEqual(updates, ['stats', 'dht'])
})

test('one failed request does not suppress a successful sibling update', async () => {
  const updates: string[] = []
  const errors: string[] = []
  const failed = applyIndependently(
    Promise.reject(new Error('offline')),
    () => updates.push('unexpected'),
    (reason) => errors.push(reason instanceof Error ? reason.message : String(reason))
  )
  const successful = applyIndependently(Promise.resolve('catalog'), (value) => {
    updates.push(value)
  })

  await Promise.allSettled([failed, successful])
  assert.deepEqual(updates, ['catalog'])
  assert.deepEqual(errors, ['offline'])
})
