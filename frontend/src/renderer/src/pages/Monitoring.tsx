import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type GeneratorStatus, type ModelInfo, type NodeInfo, type Stats } from '../api/client'

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
    try {
      const [generatorResult, modelsResult, nodesResult, statsResult] = await Promise.allSettled([
        api.getGeneratorStatus(),
        api.getModels(),
        api.getNodes(),
        api.getStats()
      ])

      setState((prev) => {
        const generator =
          generatorResult.status === 'fulfilled' ? generatorResult.value : prev.generator
        const models = modelsResult.status === 'fulfilled' ? modelsResult.value.models : prev.models
        const nodes = nodesResult.status === 'fulfilled' ? nodesResult.value.nodes ?? [] : prev.nodes
        const stats = statsResult.status === 'fulfilled' ? statsResult.value : prev.stats
        const selectedModel =
          prev.selectedModel || generator?.model_name || models[0]?.id || ''
        const failures = [generatorResult, modelsResult, nodesResult, statsResult].filter(
          (result) => result.status === 'rejected'
        )

        return {
          generator,
          models,
          nodes,
          stats,
          selectedModel,
          lastUpdated: new Date(),
          lastError: failures.length > 0 ? `${failures.length} monitor request(s) timed out` : null
        }
      })
    } catch (err) {
      setState((prev) => ({
        ...prev,
        lastError: err instanceof Error ? err.message : 'Network monitor refresh failed'
      }))
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

  const coverage = useMemo(() => {
    if (!selectedModel) {
      return { covered: 0, missing: [] as number[], percent: 0 }
    }

    const coveredLayers = new Set<number>()
    for (const node of modelNodes) {
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
      const radiusX = 250
      const radiusY = 125
      return {
        id: node.peer_id,
        label: shortPeer(node.peer_id),
        detail: `${node.layer_start}-${node.layer_end}`,
        x: 420 + Math.cos(angle) * radiusX,
        y: 190 + Math.sin(angle) * radiusY,
        kind: 'provider' as const,
        online: node.running && node.rpc_running
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
        online: Boolean(selectedModel?.runnable)
      },
      ...providers
    ]
  }, [coverage.covered, modelNodes, selectedModel, state.generator])

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
            <span className="font-mono text-[9px] text-text-dim">
              Latest completed generation
            </span>
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
                    <span className="truncate text-text-secondary">{shortPeer(hop.peer_id)}</span>
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
                  {selectedModel?.runnable
                    ? 'Complete compatible coverage is available.'
                    : (selectedModel?.route_reasons[0] ?? 'Waiting for compatible providers.')}
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
                Missing: {rangesFromMissing(selectedModel?.missing_layers ?? coverage.missing)}
              </p>
            </div>

            <div className="rounded-xl border border-border bg-bg-elevated p-5">
              <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                Route Chain
              </h2>
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
                        node.running && node.rpc_running
                          ? 'border-green/20 bg-green/5 text-green'
                          : 'border-red/20 bg-red/5 text-red'
                      }`}
                    >
                      {node.running && node.rpc_running ? 'ONLINE' : 'OFFLINE'}
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
                    {node.transport_verified === false && (
                      <span className="rounded border border-red/20 bg-red/5 px-2 py-0.5 font-mono text-[10px] text-red">
                        TRANSPORT UNVERIFIED
                      </span>
                    )}
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
