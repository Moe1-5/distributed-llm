import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type GeneratorStatus, type ModelInfo, type NetworkStatus, type NodeInfo } from '../api/client'

type BackendState = 'connecting' | 'online' | 'unreachable'

interface MonitoringState {
  backend: BackendState
  status: NetworkStatus | null
  generator: GeneratorStatus | null
  models: ModelInfo[]
  nodes: NodeInfo[]
  selectedModel: string
  lastUpdated: Date | null
  lastError: string | null
}

interface StatusPillProps {
  label: string
  ok: boolean
  detail?: string
}

function StatusPill({ label, ok, detail }: StatusPillProps): React.JSX.Element {
  return (
    <div
      className={`
        flex min-h-20 flex-col justify-between rounded-xl border p-4
        ${ok ? 'border-green/20 bg-green/5' : 'border-border bg-bg-elevated'}
      `}
    >
      <span className="font-mono text-[10px] tracking-widest text-text-dim uppercase">{label}</span>
      <span className={`mt-2 text-lg font-semibold ${ok ? 'text-green' : 'text-text-secondary'}`}>
        {ok ? 'Ready' : 'Not ready'}
      </span>
      {detail && <span className="mt-1 font-mono text-[10px] text-text-dim">{detail}</span>}
    </div>
  )
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

export default function Monitoring(): React.JSX.Element {
  const [state, setState] = useState<MonitoringState>({
    backend: 'connecting',
    status: null,
    generator: null,
    models: [],
    nodes: [],
    selectedModel: '',
    lastUpdated: null,
    lastError: null
  })

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [status, generator, modelsRes, nodesRes] = await Promise.all([
        api.getStatus(),
        api.getGeneratorStatus(),
        api.getModels(),
        api.getNodes()
      ])

      setState((prev) => {
        const selectedModel =
          prev.selectedModel || generator.model_name || modelsRes.models[0]?.id || ''

        return {
          backend: 'online',
          status,
          generator,
          models: modelsRes.models,
          nodes: nodesRes.nodes ?? [],
          selectedModel,
          lastUpdated: new Date(),
          lastError: null
        }
      })
    } catch (err) {
      setState((prev) => ({
        ...prev,
        backend: 'unreachable',
        lastError: err instanceof Error ? err.message : 'Backend unreachable'
      }))
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

  const routeTrace = state.generator?.node_trace ?? []
  const generatorReasons = state.generator?.reasons ?? []

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-7 py-5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">Monitoring</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            Network readiness, route health, and model coverage
          </p>
        </div>
        <button
          onClick={() => void refresh()}
          className="h-9 rounded-lg border border-cyan/30 bg-cyan-dim px-4 font-mono text-[10px] font-semibold text-cyan transition-colors hover:bg-cyan/20"
        >
          REFRESH
        </button>
      </div>

      <div className="flex flex-col gap-6 p-7">
        {state.backend === 'unreachable' && (
          <div className="rounded-xl border border-red/20 bg-red/5 px-5 py-4">
            <p className="font-mono text-[12px] text-red">Backend not reachable</p>
            {state.lastError && (
              <p className="mt-1 font-mono text-[10px] text-red/70">{state.lastError}</p>
            )}
          </div>
        )}

        <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          <StatusPill
            label="Backend"
            ok={state.backend === 'online'}
            detail={state.lastUpdated ? state.lastUpdated.toLocaleTimeString() : 'Waiting'}
          />
          <StatusPill
            label="Local Node"
            ok={Boolean(state.status?.node_running)}
            detail={state.status?.node_info?.model_name ?? 'No local node'}
          />
          <StatusPill
            label="Generator"
            ok={Boolean(state.status?.generator_ready)}
            detail={state.generator?.model_name ?? 'Not loaded'}
          />
          <StatusPill
            label="Route"
            ok={Boolean(state.generator?.route_ready)}
            detail={routeTrace.length > 0 ? `${routeTrace.length} hop route` : 'No route'}
          />
        </section>

        <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="rounded-xl border border-border bg-bg-elevated p-5">
            <div className="mb-5 flex items-start justify-between gap-4">
              <div>
                <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                  Layer Coverage
                </h2>
                <p className="mt-1 text-sm text-text-secondary">
                  {selectedModel?.runnable
                    ? `Runnable with ${selectedModel.compatible_nodes} compatible node(s).`
                    : (selectedModel?.route_reasons[0] ?? 'Complete compatible coverage is required.')}
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

            <div className="mb-3 flex items-center justify-between font-mono text-[10px] text-text-dim">
              <span>
                {coverage.covered} / {selectedModel?.num_layers ?? 0} layers
              </span>
              <span>{coverage.percent}% covered</span>
            </div>
            <div className="h-3 overflow-hidden rounded-full border border-border bg-bg-surface">
              <div
                className="h-full bg-cyan transition-all duration-300"
                style={{ width: `${coverage.percent}%` }}
              />
            </div>

            <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
              <div className="rounded-lg border border-border bg-bg-surface p-4">
                <p className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                  Backend Route
                </p>
                <p className="mt-2 font-mono text-[12px] text-text-secondary">
                  {selectedModel?.route_ready ? 'Ready' : 'Not runnable'}
                </p>
              </div>
              <div className="rounded-lg border border-border bg-bg-surface p-4">
                <p className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                  Missing Ranges
                </p>
                <p className="mt-2 font-mono text-[12px] text-text-secondary">
                  {rangesFromMissing(selectedModel?.missing_layers ?? coverage.missing)}
                </p>
              </div>
              <div className="rounded-lg border border-border bg-bg-surface p-4">
                <p className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                  Latency
                </p>
                <p className="mt-2 font-mono text-[12px] text-text-secondary">
                  Probe metrics pending
                </p>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-bg-elevated p-5">
            <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
              Route Trace
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
                    {hop}
                  </div>
                ))
              )}
            </div>

            {generatorReasons.length > 0 && (
              <div className="mt-5 rounded-lg border border-red/20 bg-red/5 p-3">
                <p className="font-mono text-[10px] tracking-widest text-red uppercase">
                  Readiness Reasons
                </p>
                {generatorReasons.map((reason) => (
                  <p key={reason} className="mt-2 font-mono text-[11px] text-red/80">
                    {reason}
                  </p>
                ))}
              </div>
            )}
          </div>
        </section>

        <section>
          <h2 className="mb-3 font-mono text-[10px] tracking-widest text-text-dim uppercase">
            Discovered Serving Nodes
          </h2>
          {state.nodes.length === 0 ? (
            <div className="rounded-xl border border-border bg-bg-elevated px-5 py-8 text-center">
              <p className="font-mono text-[12px] text-text-secondary">No serving nodes found</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {state.nodes.map((node) => (
                <div
                  key={`${node.peer_id}-${node.layer_start}-${node.layer_end}`}
                  className="rounded-xl border border-border bg-bg-elevated p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-[11px] font-semibold text-text-primary">
                        {node.model_name}
                      </p>
                      <p className="mt-1 truncate font-mono text-[9px] text-text-dim">
                        {node.peer_id}
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
                      {node.layer_start}
                      {' -> '}
                      {node.layer_end}
                    </span>
                    <span className="rounded border border-border px-2 py-0.5 font-mono text-[10px] text-text-dim uppercase">
                      {node.device}
                    </span>
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
