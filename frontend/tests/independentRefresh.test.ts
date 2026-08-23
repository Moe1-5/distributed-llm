import assert from 'node:assert/strict'
import test from 'node:test'

import { applyIndependently } from '../src/renderer/src/api/independentRefresh'
import { appendDiagnostic } from '../src/renderer/src/api/diagnostics'
import {
  isServingPlanAuthoritative,
  servingPlanStatus
} from '../src/renderer/src/api/servingPlanState'
import { modelPresentation, runtimeStages } from '../src/renderer/src/api/presentationState'
import type {
  GeneratorStatus,
  ModelInfo,
  NetworkRuntimeSnapshot,
  ServingPlan
} from '../src/renderer/src/api/client'

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

function model(overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    id: 'facebook/opt-125m',
    num_layers: 12,
    hidden_size: 768,
    gated: false,
    tuning: 'base',
    description: 'test',
    vram_gb: 1,
    available: true,
    local_imported: false,
    runnable: false,
    route_ready: false,
    route_reasons: [],
    covered_layers: 0,
    missing_layers: Array.from({ length: 12 }, (_, index) => index),
    total_layers: 12,
    compatible_nodes: 0,
    route_trace: [],
    ...overrides
  }
}

function generator(overrides: Partial<GeneratorStatus> = {}): GeneratorStatus {
  return {
    ready: false,
    state: 'starting',
    components_loaded: false,
    model_name: 'facebook/opt-125m',
    route_ready: false,
    reasons: [],
    node_trace: [],
    performance: null,
    health: null,
    canary: null,
    ...overrides
  }
}

function network(overrides: Partial<NetworkRuntimeSnapshot> = {}): NetworkRuntimeSnapshot {
  return {
    schema_version: 1,
    state: 'syncing',
    revision: 1,
    topology_revision: null,
    control_peer_id: 'control-peer',
    started_at: null,
    updated_at: '2026-08-23T00:00:00Z',
    last_refresh_attempt_at: null,
    last_refresh_success_at: null,
    snapshot_captured_at: null,
    snapshot_age_seconds: null,
    stale: true,
    provider_count: 0,
    retained_provider_count: 0,
    failure: null,
    validation_errors: [],
    nodes: [],
    roles: { workers: [], generator: null },
    resources: {
      control_dht: true,
      control_dht_operation_pending: false,
      control_dht_operation_uncertain: false,
      control_dht_shutdown_pending: false,
      control_dht_shutdown_failed: false,
      discovery_task: true,
      registered_workers: 0,
      publication_tasks: 0,
      health_monitors: 0
    },
    accepting_roles: true,
    ...overrides
  }
}

test('model presentation distinguishes loading, empty, stale, runnable, and gated states', () => {
  assert.equal(modelPresentation(model(), null).primary, 'Needs providers')
  assert.equal(
    modelPresentation(model({ gated: true, available: false, local_imported: false }), null)
      .primary,
    'Requires local access'
  )
  assert.equal(
    modelPresentation(model(), { snapshot_stale: true, refreshing: true } as ServingPlan).tone,
    'working'
  )
  assert.equal(
    modelPresentation(
      model(),
      {
        snapshot_stale: false,
        refreshing: false,
        current_runnable: false,
        missing_ranges: [{ start: 6, end: 12 }]
      } as ServingPlan,
      true
    ).primary,
    'Serving locally'
  )
  assert.equal(
    modelPresentation(model({ route_ready: true, runnable: true }), {
      snapshot_stale: false,
      refreshing: false,
      current_runnable: true
    } as ServingPlan).primary,
    'Can generate remotely'
  )
})

test('runtime hierarchy makes mixed-version and request-timeout failures explicit', () => {
  const mixed = runtimeStages({
    backend: 'online',
    network: null,
    generator: generator(),
    stream: 'closed',
    generationActive: false,
    diagnostics: 'idle'
  })
  assert.equal(mixed.find((stage) => stage.id === 'discovery')?.value, 'UNKNOWN')
  assert.match(mixed.find((stage) => stage.id === 'discovery')?.action ?? '', /same build/)

  const timeout = runtimeStages({
    backend: 'offline',
    network: null,
    generator: null,
    stream: 'error',
    generationActive: false,
    diagnostics: 'failed'
  })
  assert.equal(timeout.find((stage) => stage.id === 'backend')?.tone, 'failed')
  assert.equal(timeout.find((stage) => stage.id === 'websocket')?.tone, 'failed')
  assert.equal(timeout.find((stage) => stage.id === 'diagnostics')?.tone, 'failed')
})

test('runtime hierarchy distinguishes suspended state from healthy recovery', () => {
  const suspended = runtimeStages({
    backend: 'online',
    network: network({ state: 'degraded', stale: true }),
    generator: generator({
      state: 'suspended',
      components_loaded: true,
      reasons: ['selected provider went offline']
    }),
    stream: 'closed',
    generationActive: false,
    diagnostics: 'idle'
  })
  assert.equal(suspended.find((stage) => stage.id === 'generator')?.value, 'SUSPENDED')
  assert.equal(suspended.find((stage) => stage.id === 'generator')?.tone, 'failed')

  const recovered = runtimeStages({
    backend: 'online',
    network: network({ state: 'ready', stale: false, provider_count: 2 }),
    generator: generator({
      ready: true,
      state: 'ready',
      components_loaded: true,
      route_ready: true,
      canary: { ok: true },
      health: {
        enabled: true,
        route_ready: true,
        health_revision: 'healthy',
        reasons: [],
        providers: []
      }
    }),
    stream: 'open',
    generationActive: true,
    diagnostics: 'ready'
  })
  for (const id of [
    'backend',
    'discovery',
    'rpc',
    'canary',
    'generator',
    'websocket',
    'diagnostics'
  ]) {
    assert.equal(recovered.find((stage) => stage.id === id)?.tone, 'ready')
  }
  assert.equal(recovered.find((stage) => stage.id === 'generation')?.tone, 'working')
})
