/**
 * Dashboard.tsx
 * Shows real-time GPU/CPU stats and all discovered P2P nodes.
 *
 * Polling intervals:
 *   - /stats  every 2s  (fast — hardware metrics)
 *   - /nodes  every 5s  (slower — DHT network scan)
 *   - /status every 5s  (overall server state)
 */

import React, { useState, useEffect, useRef, useCallback } from 'react'
import { api, type Stats, type NodeInfo, type NetworkStatus } from '../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type BackendState = 'connecting' | 'online' | 'unreachable'

interface DashboardState {
  backend: BackendState
  status: NetworkStatus | null
  stats: Stats | null
  nodes: NodeInfo[]
  lastError: string | null
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface StatTileProps {
  label: string
  value: string | number
  unit?: string
  detail?: string
  accent?: 'cyan' | 'green' | 'red' | 'default'
  loading: boolean
}

function StatTile({
  label,
  value,
  unit,
  detail,
  accent = 'default',
  loading
}: StatTileProps): React.JSX.Element {
  const accentColor = {
    cyan: 'text-cyan',
    green: 'text-green',
    red: 'text-red',
    default: 'text-text-primary'
  }[accent]

  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-border bg-bg-elevated p-5">
      <span className="font-mono text-[10px] tracking-widest text-text-dim uppercase">{label}</span>
      {loading ? (
        <div className="h-7 w-24 animate-pulse rounded bg-bg-surface" />
      ) : (
        <div className="flex items-baseline gap-1.5">
          <span className={`text-2xl font-semibold tabular-nums ${accentColor}`}>{value}</span>
          {unit && <span className="font-mono text-xs text-text-secondary">{unit}</span>}
        </div>
      )}
      {detail && !loading && <span className="font-mono text-[10px] text-text-dim">{detail}</span>}
    </div>
  )
}

interface NodeCardProps {
  node: NodeInfo
  isLocal: boolean
  lifecycleAction: 'turning-on' | 'turning-off' | 'deleting' | null
  onTurnOn: () => void
  onTurnOff: () => void
  onDelete: () => void
}

function NodeCard({
  node,
  isLocal,
  lifecycleAction,
  onTurnOn,
  onTurnOff,
  onDelete
}: NodeCardProps): React.JSX.Element {
  const isOnline = Boolean(node.running ?? (node.layers_loaded && node.rpc_running))
  const isBusy = lifecycleAction !== null
  const canTurnOn = !isOnline && node.layers_loaded
  const toggleLabel = isOnline
    ? lifecycleAction === 'turning-off'
      ? 'TURNING OFF...'
      : 'TURN OFF'
    : lifecycleAction === 'turning-on'
      ? 'TURNING ON...'
      : 'TURN ON'
  const toggleTitle = isOnline
    ? 'Stop RPC serving but keep loaded layers in memory'
    : 'Resume RPC serving with the already-loaded layers'

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-bg-elevated p-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="font-mono text-[11px] font-semibold text-text-primary truncate">
            {node.model_name}
          </span>
          <span className="font-mono text-[9px] text-text-dim truncate" title={node.peer_id}>
            {node.peer_id.slice(0, 20)}…
          </span>
        </div>
        <span
          className={`flex-shrink-0 flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[9px] font-semibold tracking-wider ${
            isOnline ? 'border-green/20 bg-green/5 text-green' : 'border-red/20 bg-red/5 text-red'
          }`}
        >
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${
              isOnline ? 'bg-green shadow-[0_0_6px_#00ff88]' : 'bg-red'
            }`}
          />
          {isOnline ? 'ONLINE' : 'OFFLINE'}
        </span>
      </div>

      {/* Layer range */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-[9px] text-text-dim uppercase tracking-wider">Layers</span>
        <span className="rounded border border-cyan/20 bg-cyan-dim px-2 py-0.5 font-mono text-[10px] text-cyan">
          {node.layer_start} → {node.layer_end}
        </span>
        <span className="font-mono text-[9px] text-text-dim">
          ({node.layer_end - node.layer_start} blocks)
        </span>
      </div>

      {/* Device + status flags */}
      <div className="flex flex-wrap gap-1.5">
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[9px] text-text-dim uppercase">
          {node.device}
        </span>
        {isLocal && (
          <span className="rounded border border-amber/30 bg-amber/10 px-1.5 py-0.5 font-mono text-[9px] text-amber">
            LOCAL
          </span>
        )}
        {node.layers_loaded && (
          <span className="rounded border border-green/20 bg-green/5 px-1.5 py-0.5 font-mono text-[9px] text-green">
            LAYERS LOADED
          </span>
        )}
        {node.loading && (
          <span
            className="rounded border border-border px-1.5 py-0.5 font-mono text-[9px] text-text-dim"
            title={`${(node.loading.loaded_parameter_bytes / 1024 / 1024).toFixed(1)} MB of layer parameters loaded in ${node.loading.elapsed_seconds.toFixed(2)} seconds`}
          >
            {node.loading.strategy.replaceAll('_', ' ').toUpperCase()}
          </span>
        )}
        {node.rpc_running && (
          <span className="rounded border border-cyan/20 bg-cyan-dim px-1.5 py-0.5 font-mono text-[9px] text-cyan">
            RPC ACTIVE
          </span>
        )}
        {node.connection_mode && (
          <span
            className={`rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase ${
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
      </div>

      {isLocal && (
        <div className="mt-1 grid grid-cols-2 gap-2">
          <button
            onClick={isOnline ? onTurnOff : onTurnOn}
            disabled={isBusy || (!isOnline && !canTurnOn)}
            className={`
              h-9 rounded-lg border font-mono text-[10px] font-semibold transition-all
              ${
                isBusy || (!isOnline && !canTurnOn)
                  ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim'
                  : isOnline
                    ? 'border-amber/30 bg-amber/10 text-amber hover:bg-amber/20'
                    : 'border-green/20 bg-green/5 text-green hover:bg-green/10'
              }
            `}
            title={toggleTitle}
          >
            {toggleLabel}
          </button>
          <button
            onClick={onDelete}
            disabled={isBusy}
            className={`
              h-9 rounded-lg border font-mono text-[10px] font-semibold transition-all
              ${
                isBusy
                  ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim'
                  : 'border-red/30 bg-red/10 text-red hover:bg-red/20'
              }
            `}
            title="Unload layers and remove this local node"
          >
            {lifecycleAction === 'deleting' ? 'DELETING...' : 'DELETE NODE'}
          </button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function Dashboard(): React.JSX.Element {
  const [state, setState] = useState<DashboardState>({
    backend: 'connecting',
    status: null,
    stats: null,
    nodes: [],
    lastError: null
  })
  const [nodeAction, setNodeAction] = useState<{
    nodeId: string
    action: 'turning-on' | 'turning-off' | 'deleting'
  } | null>(null)

  // Use refs for interval IDs so cleanup is always correct
  const statsIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const nodesIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const statusIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const statsInFlightRef = useRef(false)
  const nodesInFlightRef = useRef(false)
  const statusInFlightRef = useRef(false)

  // ---------------------------------------------------------------------------
  // Fetch helpers — each updates only its own slice of state to avoid
  // triggering unnecessary re-renders. setState with updater fn avoids
  // stale closure issues.
  // ---------------------------------------------------------------------------

  const fetchStats = useCallback(async () => {
    if (statsInFlightRef.current) return
    statsInFlightRef.current = true
    try {
      const stats = await api.getStats()
      setState((prev) => ({ ...prev, stats, backend: 'online', lastError: null }))
    } catch (err) {
      // Only mark unreachable on stats failure — stats is the heartbeat
      setState((prev) => ({
        ...prev,
        backend: 'unreachable',
        lastError: err instanceof Error ? err.message : 'Unknown error'
      }))
    } finally {
      statsInFlightRef.current = false
    }
  }, [])

  const fetchNodes = useCallback(async () => {
    if (nodesInFlightRef.current) return
    nodesInFlightRef.current = true
    try {
      const res = await api.getLocalNodes()
      setState((prev) => ({ ...prev, nodes: res.nodes ?? [] }))
    } catch {
      // Local node lookup failure is non-fatal while the backend is starting.
      setState((prev) => ({ ...prev, nodes: [] }))
    } finally {
      nodesInFlightRef.current = false
    }
  }, [])

  const fetchStatus = useCallback(async () => {
    if (statusInFlightRef.current) return
    statusInFlightRef.current = true
    try {
      const status = await api.getStatus()
      setState((prev) => ({ ...prev, status }))
    } catch {
      // Status failure is non-fatal if stats is still succeeding
    } finally {
      statusInFlightRef.current = false
    }
  }, [])

  const refreshLocalNodeState = useCallback(async () => {
    const [statusRes, nodesRes] = await Promise.all([api.getStatus(), api.getLocalNodes()])
    setState((prev) => ({
      ...prev,
      status: statusRes,
      nodes: nodesRes.nodes ?? [],
      lastError: null
    }))
  }, [])

  const turnOnLocalNode = useCallback(async (nodeId: string) => {
    if (nodeAction) return

    setNodeAction({ nodeId, action: 'turning-on' })
    try {
      const res = await api.turnOnNode(nodeId)
      if (res.status === 'error') throw new Error(res.error ?? 'Failed to turn on local node')
      await refreshLocalNodeState()
    } catch (err) {
      setState((prev) => ({
        ...prev,
        lastError: err instanceof Error ? err.message : 'Failed to turn on local node'
      }))
    } finally {
      setNodeAction(null)
    }
  }, [nodeAction, refreshLocalNodeState])

  const turnOffLocalNode = useCallback(async (nodeId: string) => {
    if (nodeAction) return

    setNodeAction({ nodeId, action: 'turning-off' })
    try {
      const res = await api.turnOffNode(nodeId)
      if (res.status === 'error') throw new Error(res.error ?? 'Failed to turn off local node')
      await refreshLocalNodeState()
    } catch (err) {
      setState((prev) => ({
        ...prev,
        lastError: err instanceof Error ? err.message : 'Failed to turn off local node'
      }))
    } finally {
      setNodeAction(null)
    }
  }, [nodeAction, refreshLocalNodeState])

  const deleteLocalNode = useCallback(async (nodeId: string) => {
    if (nodeAction) return

    setNodeAction({ nodeId, action: 'deleting' })
    try {
      let res = await api.deleteNode(nodeId)
      if (
        res.status === 'error' &&
        res.error === 'generator_dependency_confirmation_required'
      ) {
        if (!window.confirm(res.message ?? 'Delete this node and unload its dependent generator?')) {
          return
        }
        res = await api.deleteNode(nodeId, true)
      }
      if (res.status === 'error') throw new Error(res.error ?? 'Failed to delete local node')
      await refreshLocalNodeState()
    } catch (err) {
      setState((prev) => ({
        ...prev,
        lastError: err instanceof Error ? err.message : 'Failed to delete local node'
      }))
    } finally {
      setNodeAction(null)
    }
  }, [nodeAction, refreshLocalNodeState])

  // ---------------------------------------------------------------------------
  // Mount — kick off initial fetches then set up polling
  // ---------------------------------------------------------------------------

  useEffect(() => {
    // Initial fetch sequence
    void fetchStats()
    void fetchNodes()
    void fetchStatus()

    // Set up polling intervals
    statsIntervalRef.current = setInterval(fetchStats, 2_000)
    nodesIntervalRef.current = setInterval(fetchNodes, 5_000)
    statusIntervalRef.current = setInterval(fetchStatus, 5_000)

    return () => {
      if (statsIntervalRef.current) clearInterval(statsIntervalRef.current)
      if (nodesIntervalRef.current) clearInterval(nodesIntervalRef.current)
      if (statusIntervalRef.current) clearInterval(statusIntervalRef.current)
    }
  }, [fetchStats, fetchNodes, fetchStatus])

  // ---------------------------------------------------------------------------
  // Derived values
  // ---------------------------------------------------------------------------

  const { backend, status, stats, nodes, lastError } = state
  const isLoading = backend === 'connecting'
  const onlineCount = nodes.filter((n) => n.running && n.rpc_running).length
  const offlineCount = nodes.length - onlineCount
  const localNodeIds = new Set(status?.local_node_ids ?? [])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-7 py-5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">Local Nodes</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            Serving processes and hardware on this machine
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1.5 rounded-full border border-green/20 bg-green/5 px-3 py-1.5 font-mono text-[10px] text-green">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-green" />
            {onlineCount} online
          </span>
          <span className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 font-mono text-[10px] text-text-dim">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-text-dim" />
            {offlineCount} offline
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-6 p-7">
        {/* Error banner — only shown when backend is truly unreachable */}
        {backend === 'unreachable' && (
          <div className="rounded-xl border border-red/20 bg-red/5 px-5 py-4">
            <p className="font-mono text-[12px] text-red">
              Backend not reachable — make sure the backend is running on port 8000
            </p>
            {lastError && <p className="mt-1 font-mono text-[10px] text-red/60">{lastError}</p>}
          </div>
        )}

        {/* Generator ready banner */}
        {status?.generator_ready && (
          <div className="rounded-xl border border-green/20 bg-green/5 px-5 py-4">
            <p className="font-mono text-[12px] text-green">
              Generator ready — go to Inference page to start chatting
            </p>
          </div>
        )}
        {status?.generator_components_loaded && !status.generator_ready && (
          <div className="rounded-xl border border-amber/20 bg-amber/5 px-5 py-4">
            <p className="font-mono text-[12px] text-amber">
              Generator suspended — complete RPC-healthy coverage is required
            </p>
            {status.generator_reasons?.length > 0 && (
              <p className="mt-1 font-mono text-[10px] text-amber/70">
                {status.generator_reasons.join('; ')}
              </p>
            )}
          </div>
        )}

        {/* Hardware stats */}
        <section>
          <h2 className="mb-3 font-mono text-[10px] tracking-widest text-text-dim uppercase">
            This Machine
          </h2>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="CPU"
              value={stats ? stats.cpu_percent.toFixed(1) : '—'}
              unit="%"
              loading={isLoading}
            />
            <StatTile
              label="RAM"
              value={stats ? stats.ram_percent.toFixed(1) : '—'}
              unit="%"
              detail={
                stats
                  ? `${stats.ram_used_gb.toFixed(1)} / ${stats.ram_total_gb.toFixed(1)} GB`
                  : undefined
              }
              loading={isLoading}
            />
            <StatTile
              label="GPU"
              value={stats?.gpu?.util_percent != null ? stats.gpu.util_percent.toFixed(1) : '—'}
              unit="%"
              detail={
                stats?.gpu
                  ? `${stats.gpu.name}${stats.gpu.util_percent == null ? ' · utilization unavailable' : ''}`
                  : 'No GPU'
              }
              accent={stats?.gpu?.util_percent != null ? 'cyan' : 'default'}
              loading={isLoading}
            />
            <StatTile
              label="VRAM"
              value={stats?.gpu ? stats.gpu.vram_percent.toFixed(1) : '—'}
              unit="%"
              detail={
                stats?.gpu
                  ? `${stats.gpu.vram_used_gb.toFixed(1)} / ${stats.gpu.vram_total_gb.toFixed(1)} GB`
                  : 'No GPU'
              }
              accent={stats?.gpu ? 'cyan' : 'default'}
              loading={isLoading}
            />
          </div>
        </section>

        {/* Node grid */}
        <section>
          <h2 className="mb-3 font-mono text-[10px] tracking-widest text-text-dim uppercase">
            Local Serving Nodes
          </h2>

          {backend === 'online' && nodes.length === 0 && (
            <div className="rounded-xl border border-border bg-bg-elevated px-5 py-8 text-center">
              <p className="font-mono text-[12px] text-text-secondary">No local nodes configured</p>
              <p className="mt-1 font-mono text-[10px] text-text-dim">
                Start a node on the Network page to manage it here
              </p>
            </div>
          )}

          {nodes.length > 0 && (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {nodes.map((node) => {
                const lifecycleAction =
                  nodeAction && nodeAction.nodeId === node.node_id ? nodeAction.action : null
                return (
                  <NodeCard
                    key={`${node.node_id ?? node.peer_id}-${node.layer_start}-${node.layer_end}`}
                    node={node}
                    isLocal={Boolean(node.node_id && localNodeIds.has(node.node_id))}
                    lifecycleAction={lifecycleAction}
                    onTurnOn={() => node.node_id && void turnOnLocalNode(node.node_id)}
                    onTurnOff={() => node.node_id && void turnOffLocalNode(node.node_id)}
                    onDelete={() => node.node_id && void deleteLocalNode(node.node_id)}
                  />
                )
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
