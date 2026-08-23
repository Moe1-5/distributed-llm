import assert from 'node:assert/strict'
import test from 'node:test'

import { applyIndependently } from '../src/renderer/src/api/independentRefresh'
import { appendDiagnostic } from '../src/renderer/src/api/diagnostics'
import {
  isServingPlanAuthoritative,
  servingPlanStatus
} from '../src/renderer/src/api/servingPlanState'
import type { ServingPlan } from '../src/renderer/src/api/client'

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

function planState(snapshotStale: boolean, refreshing: boolean): ServingPlan {
  return { snapshot_stale: snapshotStale, refreshing } as ServingPlan
}

test('only a fresh completed serving-plan refresh is authoritative', () => {
  assert.equal(isServingPlanAuthoritative(planState(true, true)), false)
  assert.equal(isServingPlanAuthoritative(planState(true, false)), false)
  assert.equal(isServingPlanAuthoritative(planState(false, true)), false)
  assert.equal(isServingPlanAuthoritative(planState(false, false)), true)
  assert.equal(
    isServingPlanAuthoritative({
      ...planState(false, false),
      placement: {
        enabled: true,
        authoritative: false,
        capacity_available: false,
        topology_revision: 4,
        model_revision: 'main',
        captured_at: '2026-08-23T00:00:00Z',
        reservations: []
      }
    }),
    false
  )
  assert.equal(servingPlanStatus(planState(true, true), null, false), 'discovering')
  assert.equal(servingPlanStatus(planState(false, false), null, false), 'fresh')
  assert.equal(
    servingPlanStatus(planState(false, false), 'request timed out', false),
    'unavailable'
  )
})

test('diagnostics are bounded, redact secrets, and collapse consecutive duplicates', () => {
  const first = appendDiagnostic(
    [],
    {
      source: 'api',
      severity: 'error',
      summary: 'request failed',
      details: { path: '/settings/token', token: 'hf_secret' }
    },
    '2026-08-22T00:00:00.000Z',
    'one'
  )
  const repeated = appendDiagnostic(
    first,
    {
      source: 'api',
      severity: 'error',
      summary: 'request failed',
      details: { path: '/settings/token', token: 'a different secret' }
    },
    '2026-08-22T00:00:01.000Z',
    'two'
  )

  assert.equal(repeated.length, 1)
  assert.equal(repeated[0].occurrences, 2)
  assert.equal(repeated[0].details.token, '[redacted]')

  const bounded = Array.from(
    { length: 205 },
    (_, index) =>
      appendDiagnostic(
        [],
        { source: 'network', severity: 'info', summary: `event ${index}` },
        `2026-08-22T00:00:${String(index % 60).padStart(2, '0')}.000Z`,
        String(index)
      )[0]
  )
  const result = appendDiagnostic(bounded, {
    source: 'network',
    severity: 'success',
    summary: 'last event'
  })
  assert.equal(result.length, 200)
  assert.equal(result.at(-1)?.summary, 'last event')
})
