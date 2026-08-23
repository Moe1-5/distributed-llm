import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type GeneratorStatus, type ModelInfo, type NodeInfo, type Stats } from '../api/client'
import { applyIndependently } from '../api/independentRefresh'

interface MonitoringState {
  generator: GeneratorStatus | null
  models: ModelInfo[]
  nodes: NodeInfo[]
  stats: Stats | null
  selectedModel: string
  lastUpdated: Date | null
  lastError: string | null
}

interface MapNode {
  id: string
  label: string
  detail: string
  x: number
  y: number
  kind: 'client' | 'model' | 'provider'
  online: boolean
}

function rangesFromMissing(missing: number[]): string {
  if (missing.length === 0) return 'None'

  const ranges: string[] = []
  let start = missing[0]
  let previous = missing[0]

  for (const layer of missing.slice(1)) {
    if (layer === previous + 1) {
      previous = layer
      continue
    }
    ranges.push(start === previous ? `${start}` : `${start}-${previous + 1}`)
    start = layer
    previous = layer
  }

  ranges.push(start === previous ? `${start}` : `${start}-${previous + 1}`)
  return ranges.join(', ')
}

function shortPeer(peerId: string): string {
  return peerId.length <= 10 ? peerId : `${peerId.slice(0, 8)}...`
}

function fixedMetric(value: unknown, digits: number): string | null {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : null
}

function formatVram(stats: Stats | null): string {
  const allocated = fixedMetric(stats?.gpu?.vram_used_gb, 2)
  if (allocated === null) return 'Unavailable'

  const reserved = fixedMetric(stats?.gpu?.vram_reserved_gb, 2)
  return reserved === null
    ? `${allocated} GB allocated`
    : `${allocated} GB allocated · ${reserved} GB reserved`
}

function formatDuration(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Unavailable'
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`
  return `${value.toFixed(1)} ms`
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Unavailable'
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value.toFixed(0)} B`
}

export default function Monitoring(): React.JSX.Element {
  const [state, setState] = useState<MonitoringState>({
    generator: null,
    models: [],
    nodes: [],
    stats: null,
    selectedModel: '',
    lastUpdated: null,
    lastError: null
  })

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const refreshInFlightRef = useRef(false)

  const refresh = useCallback(async () => {
    if (refreshInFlightRef.current) return
    refreshInFlightRef.current = true
    setState((prev) => ({ ...prev, lastError: null }))
    let failures = 0
    const failed = (): void => {
      failures += 1
      setState((prev) => ({
        ...prev,
        lastError: `${failures} monitor request(s) timed out`
      }))
    }
    const requests = [
      applyIndependently(
        api.getGeneratorStatus(),
        (generator) => {
          setState((prev) => ({
            ...prev,
            generator,
            selectedModel: prev.selectedModel || generator.model_name || '',
            lastUpdated: new Date()
          }))
        },
        failed
      ),
      // Monitoring only needs the stable model catalog here. The full /models
      // endpoint performs a route scan for every supported model and can be
      // slower than the five-second monitoring refresh cadence over a relay.
      applyIndependently(
        api.getModelCatalog(),
        (result) => {
          setState((prev) => ({
            ...prev,
            models: result.models,
            selectedModel: prev.selectedModel || result.models[0]?.id || '',
            lastUpdated: new Date()
          }))
        },
        failed
      ),
      applyIndependently(
        api.getNodes(),
        (result) => {
          setState((prev) => ({
            ...prev,
            nodes: result.nodes ?? [],
            lastUpdated: new Date()
          }))
        },
        failed
      ),
      applyIndependently(
        api.getStats(),
        (stats) => {
          setState((prev) => ({ ...prev, stats, lastUpdated: new Date() }))
        },
        failed
      )
    ]
    try {
      await Promise.allSettled(requests)
    } finally {
      refreshInFlightRef.current = false
    }
  }, [])

  useEffect(() => {
    void refresh()
    intervalRef.current = setInterval(refresh, 5_000)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [refresh])

  const selectedModel = state.models.find((model) => model.id === state.selectedModel)
  const modelNodes = state.nodes.filter((node) => node.model_name === state.selectedModel)
  const routeTrace = state.generator?.node_trace ?? []
  const generatorPerformance = state.generator?.performance
  const lastGeneration = generatorPerformance?.last_generation
  const activeRoute = state.generator?.health?.active_route
  const alternateRoutes = state.generator?.health?.alternate_routes ?? []
  const lastFailover = state.generator?.health?.last_failover
  const healthByProvider = useMemo(
    () =>
      new Map(
        (state.generator?.health?.providers ?? []).map((health) => [
          `${health.peer_id}:${health.rpc_uid}`,
          health
        ])
      ),
    [state.generator?.health?.providers]
  )

  const coverage = useMemo(() => {
    if (!selectedModel) {
      return { covered: 0, missing: [] as number[], percent: 0 }
    }

    const coveredLayers = new Set<number>()
    for (const node of modelNodes) {
      if (!node.running || !node.layers_loaded || !node.rpc_running) continue
      for (let layer = node.layer_start; layer < node.layer_end; layer += 1) {
        if (layer >= 0 && layer < selectedModel.num_layers) coveredLayers.add(layer)
      }
    }

    const missing = Array.from({ length: selectedModel.num_layers }, (_, layer) => layer).filter(
      (layer) => !coveredLayers.has(layer)
    )

    return {
      covered: coveredLayers.size,
      missing,
      percent:
        selectedModel.num_layers === 0
          ? 0
          : Math.round((coveredLayers.size / selectedModel.num_layers) * 100)
    }
  }, [modelNodes, selectedModel])

  const mapNodes = useMemo<MapNode[]>(() => {
    const providerCount = Math.max(modelNodes.length, 1)
    const providers = modelNodes.map((node, index) => {
      const angle = -Math.PI / 2 + (index / providerCount) * Math.PI * 2
      const health = healthByProvider.get(`${node.peer_id}:${node.rpc_uid ?? ''}`)
      const rpcReachable = health
        ? health.state === 'healthy' || health.state === 'degraded'
        : node.running && node.rpc_running
      const radiusX = 250
      const radiusY = 125
      return {
        id: node.peer_id,
        label: shortPeer(node.peer_id),
        detail: `${node.layer_start}-${node.layer_end}`,
        x: 420 + Math.cos(angle) * radiusX,
        y: 190 + Math.sin(angle) * radiusY,
        kind: 'provider' as const,
        online: rpcReachable
      }
    })

    return [
      {
        id: 'client',
        label: 'Client',
        detail: state.generator?.model_name ?? 'Generator',
        x: 92,
        y: 190,
        kind: 'client',
        online: Boolean(state.generator?.ready)
      },
      {
        id: 'model',
        label: selectedModel?.id ?? 'Model',
        detail: `${coverage.covered}/${selectedModel?.num_layers ?? 0} layers`,
        x: 420,
        y: 190,
        kind: 'model',
        online: Boolean(selectedModel && coverage.percent === 100)
      },
      ...providers
    ]
  }, [
    coverage.covered,
    coverage.percent,
    healthByProvider,
    modelNodes,
    selectedModel,
    state.generator
  ])

  const providerNodes = mapNodes.filter((node) => node.kind === 'provider')

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-7 py-5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">Monitoring</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            P2P route map, layer coverage, and serving-node visibility
          </p>
        </div>
        <div className="flex items-center gap-3">
          {state.lastUpdated && (
            <span className="font-mono text-[10px] text-text-dim">
              {state.lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={() => void refresh()}
            className="h-9 rounded-lg border border-cyan/30 bg-cyan-dim px-4 font-mono text-[10px] font-semibold text-cyan transition-colors hover:bg-cyan/20"
          >
            REFRESH
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-6 p-7">
        {state.lastError && (
          <div className="rounded-xl border border-red/20 bg-red/5 px-5 py-4">
            <p className="font-mono text-[12px] text-red">{state.lastError}</p>
          </div>
        )}

        <section>
          <div className="mb-3 flex items-end justify-between gap-4">
            <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
              Runtime Performance
            </h2>
            <span className="font-mono text-[9px] text-text-dim">
              {state.stats?.sampled_at
                ? `sampled ${new Date(state.stats.sampled_at).toLocaleTimeString()}`
                : 'waiting for first sample'}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
            {[
              [
                'System CPU',
                fixedMetric(state.stats?.cpu_percent, 1) !== null
                  ? `${fixedMetric(state.stats?.cpu_percent, 1)}%`
                  : '—'
              ],
              [
                'System RAM',
                fixedMetric(state.stats?.ram_percent, 1) !== null
                  ? `${fixedMetric(state.stats?.ram_percent, 1)}%`
                  : '—'
              ],
              [
                'Backend RAM',
                fixedMetric(state.stats?.process?.rss_gb, 3) !== null
                  ? `${fixedMetric(state.stats?.process?.rss_gb, 3)} GB`
                  : '—'
              ],
              [
                'Backend CPU',
                fixedMetric(state.stats?.process?.cpu_percent, 1) !== null
                  ? `${fixedMetric(state.stats?.process?.cpu_percent, 1)}%`
                  : '—'
              ],
              [
                'GPU',
                state.stats?.gpu?.util_percent != null
                  ? `${state.stats.gpu.util_percent.toFixed(1)}%`
                  : 'Unavailable'
              ],
              ['VRAM', formatVram(state.stats)]
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border border-border bg-bg-elevated p-4">
                <p className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                  {label}
                </p>
                <p className="mt-2 text-lg font-semibold tabular-nums text-text-primary">{value}</p>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="mb-3 flex items-end justify-between gap-4">
            <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
              Generation Performance
            </h2>
            <span className="font-mono text-[9px] text-text-dim">Latest completed generation</span>
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
            {[
              ['Startup', formatDuration(generatorPerformance?.startup_duration_ms)],
              ['Model load', formatDuration(generatorPerformance?.load_duration_ms)],
              ['Route check', formatDuration(generatorPerformance?.route_validation_ms)],
              ['First token', formatDuration(lastGeneration?.time_to_first_token_ms)],
              ['Total', formatDuration(lastGeneration?.total_duration_ms)],
              [
                'Throughput',
                lastGeneration
                  ? `${lastGeneration.tokens_per_second.toFixed(2)} tok/s`
                  : 'Unavailable'
              ]
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-border bg-bg-elevated p-4">
                <p className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                  {label}
                </p>
                <p className="mt-2 text-lg font-semibold tabular-nums text-text-primary">{value}</p>
              </div>
            ))}
          </div>

          {lastGeneration?.session_protocol_version === 1 && (
            <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
              {[
                ['Session', 'Protocol v1'],
                ['Prefill wire', formatBytes(lastGeneration.session_prefill_bytes)],
                ['Decode wire', formatBytes(lastGeneration.session_decode_bytes)],
                ['Decode average', formatDuration(lastGeneration.session_average_decode_ms)],
                [
                  'Peak remote cache',
                  formatBytes(lastGeneration.session_peak_provider_cache_bytes)
                ],
                ['Route rebuilds', String(lastGeneration.session_rebuilds ?? 0)]
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-cyan/20 bg-cyan-dim p-4">
                  <p className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                    {label}
                  </p>
                  <p className="mt-2 text-sm font-semibold tabular-nums text-cyan">{value}</p>
                </div>
              ))}
            </div>
          )}

          {lastGeneration && (
            <div className="mt-3 overflow-hidden rounded-lg border border-border bg-bg-elevated">
              <div className="grid grid-cols-[minmax(0,1fr)_100px_90px_110px] gap-3 border-b border-border px-4 py-2 font-mono text-[9px] tracking-widest text-text-dim uppercase">
                <span>Relay hop</span>
                <span>Layers</span>
                <span>Calls</span>
                <span>Average</span>
              </div>
              {lastGeneration.hop_metrics.length === 0 ? (
                <p className="px-4 py-3 font-mono text-[11px] text-text-dim">
                  No completed RPC hop measurements yet.
                </p>
              ) : (
                lastGeneration.hop_metrics.map((hop) => (
                  <div
                    key={`${hop.peer_id}-${hop.layer_start}-${hop.layer_end}`}
                    className="grid grid-cols-[minmax(0,1fr)_100px_90px_110px] gap-3 border-b border-border px-4 py-2.5 font-mono text-[11px] last:border-b-0"
                  >
                    <span
                      className="truncate text-text-secondary"
                      title={`Selected ${hop.selected_peer_id ?? hop.peer_id}; executed ${hop.executed_peer_id ?? hop.peer_id}`}
                    >
                      {shortPeer(hop.selected_peer_id ?? hop.peer_id)}
                      {(hop.executed_peer_id ?? hop.peer_id) !==
                        (hop.selected_peer_id ?? hop.peer_id) &&
                        ` → ${shortPeer(hop.executed_peer_id ?? hop.peer_id)}`}
                    </span>
                    <span className="text-cyan">
                      {hop.layer_start}-{hop.layer_end}
                    </span>
                    <span className="tabular-nums text-text-secondary">{hop.calls}</span>
                    <span className="tabular-nums text-text-primary">
                      {formatDuration(hop.average_latency_ms)}
                    </span>
                  </div>
                ))
              )}
            </div>
          )}
        </section>

        <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="rounded-xl border border-border bg-bg-elevated p-5">
            <div className="mb-5 flex items-start justify-between gap-4">
              <div>
                <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                  Network Map
                </h2>
                <p className="mt-1 text-sm text-text-secondary">
                  {coverage.percent === 100
                    ? 'Complete compatible coverage is available.'
                    : `Waiting for compatible providers; missing ${rangesFromMissing(coverage.missing)}.`}
                </p>
              </div>
              <select
                value={state.selectedModel}
                onChange={(event) =>
                  setState((prev) => ({ ...prev, selectedModel: event.target.value }))
                }
                className="h-9 min-w-64 rounded-lg border border-border-bright bg-bg-surface px-3 font-mono text-[11px] text-text-primary outline-none focus:border-cyan/40"
              >
                {state.models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.id}
                  </option>
                ))}
              </select>
            </div>

            <div className="relative h-[380px] overflow-hidden rounded-lg border border-border bg-bg-base">
              <svg viewBox="0 0 760 380" className="h-full w-full">
                <defs>
                  <filter id="soft-glow">
                    <feGaussianBlur stdDeviation="3" result="blur" />
                    <feMerge>
                      <feMergeNode in="blur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>

                <line x1="92" y1="190" x2="420" y2="190" stroke="#2a3a4d" strokeWidth="1.5" />
                {providerNodes.map((node) => (
                  <line
                    key={`${node.id}-edge`}
                    x1="420"
                    y1="190"
                    x2={node.x}
                    y2={node.y}
                    stroke={node.online ? '#00d4ff70' : '#2a3a4d'}
                    strokeWidth="1.5"
                  />
                ))}

                {mapNodes.map((node) => {
                  const fill =
                    node.kind === 'client'
                      ? '#00d4ff'
                      : node.kind === 'model'
                        ? '#ffaa00'
                        : node.online
                          ? '#00ff88'
                          : '#3a4a5a'
                  const radius = node.kind === 'model' ? 24 : node.kind === 'client' ? 20 : 16

                  return (
                    <g key={node.id}>
                      <circle
                        cx={node.x}
                        cy={node.y}
                        r={radius}
                        fill={fill}
                        opacity={node.online ? 0.92 : 0.55}
                        filter={node.online ? 'url(#soft-glow)' : undefined}
                      />
                      <text
                        x={node.x}
                        y={node.y + radius + 18}
                        textAnchor="middle"
                        fill="#e8edf2"
                        fontSize="11"
                        fontFamily="IBM Plex Mono, monospace"
                      >
                        {node.label}
                      </text>
                      <text
                        x={node.x}
                        y={node.y + radius + 34}
                        textAnchor="middle"
                        fill="#7a8a9a"
                        fontSize="10"
                        fontFamily="IBM Plex Mono, monospace"
                      >
                        {node.detail}
                      </text>
                    </g>
                  )
                })}
              </svg>
            </div>
          </div>

          <div className="flex flex-col gap-4">
            <div className="rounded-xl border border-border bg-bg-elevated p-5">
              <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                Layer Coverage
              </h2>
              <div className="mt-4 flex items-end justify-between">
                <span className="text-3xl font-semibold text-text-primary">
                  {coverage.percent}%
                </span>
                <span className="font-mono text-[11px] text-text-dim">
                  {coverage.covered}/{selectedModel?.num_layers ?? 0}
                </span>
              </div>
              <div className="mt-4 h-3 overflow-hidden rounded-full border border-border bg-bg-surface">
                <div
                  className="h-full bg-cyan transition-all duration-300"
                  style={{ width: `${coverage.percent}%` }}
                />
              </div>
              <p className="mt-4 font-mono text-[11px] text-text-secondary">
                Missing: {rangesFromMissing(coverage.missing)}
              </p>
            </div>

            <div className="rounded-xl border border-border bg-bg-elevated p-5">
              <div className="flex items-start justify-between gap-3">
                <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                  Route Chain
                </h2>
                {activeRoute && (
                  <span
                    className={`rounded border px-2 py-0.5 font-mono text-[9px] uppercase ${
                      activeRoute.degraded
                        ? 'border-amber/30 bg-amber/10 text-amber'
                        : 'border-green/20 bg-green/5 text-green'
                    }`}
                  >
                    {activeRoute.transport} · {activeRoute.degraded ? 'degraded' : 'healthy'}
                  </span>
                )}
              </div>
              <div className="mt-4 flex flex-col gap-2">
                {routeTrace.length === 0 ? (
                  <p className="font-mono text-[11px] text-text-dim">No validated route yet.</p>
                ) : (
                  routeTrace.map((hop, index) => (
                    <div
                      key={`${hop}-${index}`}
                      className="rounded-lg border border-cyan/20 bg-cyan-dim px-3 py-2 font-mono text-[11px] text-cyan"
                    >
                      {index + 1}. {hop}
                    </div>
                  ))
                )}
              </div>
              <div className="mt-4 border-t border-border pt-4">
                <div className="flex items-center justify-between gap-3 font-mono text-[10px]">
                  <span className="text-text-dim uppercase">Complete alternates</span>
                  <span className="text-text-primary">{alternateRoutes.length}</span>
                </div>
                {alternateRoutes.map((candidate, index) => (
                  <p
                    key={`${candidate.transport}-${index}`}
                    className="mt-2 font-mono text-[10px] text-text-secondary"
                  >
                    {index + 1}.{' '}
                    {candidate.route
                      .map(
                        (node) =>
                          `${node.layer_start}-${node.layer_end} @ ${shortPeer(node.peer_id)}`
                      )
                      .join(' → ')}{' '}
                    · {candidate.transport}
                  </p>
                ))}
                {lastFailover && (
                  <p
                    className={`mt-3 font-mono text-[10px] ${
                      lastFailover.failed_over ? 'text-amber' : 'text-text-dim'
                    }`}
                  >
                    Last forward: {lastFailover.attempt_count || 0} attempt(s)
                    {lastFailover.failed_over
                      ? lastFailover.reasons.at(-1)?.phase === 'session_pre_dispatch'
                        ? ' · session rebuilt from known history'
                        : ` · failed over at layers ${lastFailover.reasons.at(-1)?.layer_start}-${lastFailover.reasons.at(-1)?.layer_end}`
                      : ' · no failover'}
                  </p>
                )}
              </div>
            </div>
          </div>
        </section>

        <section>
          <h2 className="mb-3 font-mono text-[10px] tracking-widest text-text-dim uppercase">
            Serving Providers
          </h2>
          {modelNodes.length === 0 ? (
            <div className="rounded-xl border border-border bg-bg-elevated px-5 py-8 text-center">
              <p className="font-mono text-[12px] text-text-secondary">
                No providers for the selected model.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {modelNodes.map((node) => (
                <div
                  key={`${node.peer_id}-${node.layer_start}-${node.layer_end}`}
                  className="rounded-xl border border-border bg-bg-elevated p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-[11px] font-semibold text-text-primary">
                        {shortPeer(node.peer_id)}
                      </p>
                      <p className="mt-1 truncate font-mono text-[9px] text-text-dim">
                        {node.model_name}
                      </p>
                    </div>
                    <span
                      className={`rounded-full border px-2 py-0.5 font-mono text-[9px] ${
                        node.running && node.rpc_running && node.rpc_publication?.fresh !== false
                          ? 'border-green/20 bg-green/5 text-green'
                          : 'border-red/20 bg-red/5 text-red'
                      }`}
                    >
                      {node.running && node.rpc_running && node.rpc_publication?.fresh !== false
                        ? 'DHT ADVERTISED'
                        : 'DHT STALE'}
                    </span>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-1.5">
                    <span className="rounded border border-cyan/20 bg-cyan-dim px-2 py-0.5 font-mono text-[10px] text-cyan">
                      layers {node.layer_start}-{node.layer_end}
                    </span>
                    <span className="rounded border border-border px-2 py-0.5 font-mono text-[10px] text-text-dim uppercase">
                      {node.device}
                    </span>
                    {node.connection_mode && (
                      <span
                        className={`rounded border px-2 py-0.5 font-mono text-[10px] uppercase ${
                          node.connection_mode === 'relay'
                            ? 'border-amber/30 bg-amber/10 text-amber'
                            : node.connection_mode === 'direct'
                              ? 'border-green/20 bg-green/5 text-green'
                              : 'border-border text-text-dim'
                        }`}
                      >
                        {node.connection_mode}
                      </span>
                    )}
                    {(() => {
                      const role = healthByProvider.get(
                        `${node.peer_id}:${node.rpc_uid ?? ''}`
                      )?.route_role
                      if (!role) return null
                      const roleClass =
                        role === 'active'
                          ? 'border-cyan/30 bg-cyan-dim text-cyan'
                          : role === 'alternate'
                            ? 'border-amber/30 bg-amber/10 text-amber'
                            : 'border-border text-text-dim'
                      return (
                        <span
                          className={`rounded border px-2 py-0.5 font-mono text-[10px] uppercase ${roleClass}`}
                        >
                          {role}
                        </span>
                      )
                    })()}
                    {node.loading && (
                      <span
                        className={`rounded border px-2 py-0.5 font-mono text-[10px] uppercase ${
                          node.loading.strategy === 'selective_safetensors'
                            ? 'border-green/20 bg-green/5 text-green'
                            : 'border-amber/30 bg-amber/10 text-amber'
                        }`}
                        title={node.loading.fallback_reason ?? undefined}
                      >
                        {node.loading.strategy === 'selective_safetensors'
                          ? 'SELECTIVE LOAD'
                          : 'FULL LOAD FALLBACK'}
                      </span>
                    )}
                    {node.rpc_safety && (
                      <span
                        className={`rounded border px-2 py-0.5 font-mono text-[10px] ${
                          node.rpc_safety.failed_requests > 0 ||
                          node.rpc_safety.rejected_requests > 0 ||
                          node.rpc_safety.timed_out_requests > 0
                            ? 'border-amber/30 bg-amber/10 text-amber'
                            : 'border-border text-text-dim'
                        }`}
                        title={`Active ${node.rpc_safety.active_forwards}/${node.rpc_safety.policy.max_concurrent_forwards}; queued ${node.rpc_safety.queued_forwards}/${node.rpc_safety.policy.max_queued_forwards}`}
                      >
                        RPC {node.rpc_safety.completed_requests} OK /{' '}
                        {node.rpc_safety.failed_requests} FAILED /{' '}
                        {node.rpc_safety.rejected_requests} REJECTED /{' '}
                        {node.rpc_safety.timed_out_requests} TIMEOUT
                      </span>
                    )}
                    {node.session_cache?.supported && (
                      <span
                        className={`rounded border px-2 py-0.5 font-mono text-[10px] ${
                          (node.session_cache.evicted_sessions ?? 0) > 0 ||
                          (node.session_cache.admission_rejections ?? 0) > 0 ||
                          (node.session_cache.replay_rejections ?? 0) > 0
                            ? 'border-amber/30 bg-amber/10 text-amber'
                            : 'border-cyan/20 bg-cyan-dim text-cyan'
                        }`}
                        title={`Active ${node.session_cache.active_sessions}/${node.session_cache.policy?.max_sessions ?? '?'}; cache ${formatBytes(node.session_cache.estimated_cache_bytes)}`}
                      >
                        SESSION {node.session_cache.active_sessions} ACTIVE /{' '}
                        {node.session_cache.evicted_sessions ?? 0} EVICTED /{' '}
                        {node.session_cache.admission_rejections ?? 0} REJECTED
                      </span>
                    )}
                    {node.loading && (
                      <span className="rounded border border-border px-2 py-0.5 font-mono text-[10px] text-text-dim">
                        {(node.loading.loaded_parameter_bytes / 1024 / 1024).toFixed(1)} MB /{' '}
                        {node.loading.elapsed_seconds.toFixed(2)}s
                      </span>
                    )}
                    {node.transport_verified === false && (
                      <span className="rounded border border-red/20 bg-red/5 px-2 py-0.5 font-mono text-[10px] text-red">
                        TRANSPORT UNVERIFIED
                      </span>
                    )}
                    {(() => {
                      const health = healthByProvider.get(`${node.peer_id}:${node.rpc_uid ?? ''}`)
                      if (!health) {
                        const leaseFresh = node.rpc_publication?.fresh
                        return (
                          <span
                            className={`rounded border px-2 py-0.5 font-mono text-[10px] ${
                              leaseFresh
                                ? 'border-cyan/20 bg-cyan-dim text-cyan'
                                : 'border-border text-text-dim'
                            }`}
                            title="A generator has not actively probed this provider in the current backend."
                          >
                            {leaseFresh ? 'RPC LEASE FRESH · NOT PROBED' : 'RPC NOT PROBED'}
                          </span>
                        )
                      }
                      const stateClass =
                        health.state === 'healthy'
                          ? 'border-green/20 bg-green/5 text-green'
                          : health.state === 'checking'
                            ? 'border-border text-text-dim'
                            : health.state === 'degraded'
                              ? 'border-amber/30 bg-amber/10 text-amber'
                              : 'border-red/20 bg-red/5 text-red'
                      return (
                        <>
                          <span
                            className={`rounded border px-2 py-0.5 font-mono text-[10px] uppercase ${stateClass}`}
                            title={health.reason ?? undefined}
                          >
                            RPC {health.state}
                            {health.latency_ms !== null
                              ? ` / ${health.latency_ms.toFixed(0)}ms`
                              : ''}
                          </span>
                          {health.state !== 'healthy' && health.reason && (
                            <span
                              className="basis-full truncate font-mono text-[9px] text-text-dim"
                              title={health.reason}
                            >
                              {health.reason}
                            </span>
                          )}
                        </>
                      )
                    })()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
