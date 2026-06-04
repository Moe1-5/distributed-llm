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
}

function NodeCard({ node }: NodeCardProps): React.JSX.Element {
  const isOnline = node.running && node.rpc_running

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
        {node.layers_loaded && (
          <span className="rounded border border-green/20 bg-green/5 px-1.5 py-0.5 font-mono text-[9px] text-green">
            LAYERS LOADED
          </span>
        )}
        {node.rpc_running && (
          <span className="rounded border border-cyan/20 bg-cyan-dim px-1.5 py-0.5 font-mono text-[9px] text-cyan">
            RPC ACTIVE
          </span>
        )}
      </div>
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

  // Use refs for interval IDs so cleanup is always correct
  const statsIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const nodesIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const statusIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ---------------------------------------------------------------------------
  // Fetch helpers — each updates only its own slice of state to avoid
  // triggering unnecessary re-renders. setState with updater fn avoids
  // stale closure issues.
  // ---------------------------------------------------------------------------

  const fetchStats = useCallback(async () => {
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
    }
  }, [])

  const fetchNodes = useCallback(async () => {
    try {
      const res = await api.getNodes()
      // /nodes returns empty array with a warning when no DHT yet — not an error
      setState((prev) => ({ ...prev, nodes: res.nodes ?? [] }))
    } catch {
      // Node discovery failure is non-fatal — DHT may not be started yet
      setState((prev) => ({ ...prev, nodes: [] }))
    }
  }, [])

  const fetchStatus = useCallback(async () => {
    try {
      const status = await api.getStatus()
      setState((prev) => ({ ...prev, status }))
    } catch {
      // Status failure is non-fatal if stats is still succeeding
    }
  }, [])

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

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-7 py-5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">Network Nodes</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            Distributed layer allocation across P2P peers
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
              value={stats?.gpu ? stats.gpu.util_percent.toFixed(1) : '—'}
              unit="%"
              detail={stats?.gpu?.name ?? 'No GPU'}
              accent={stats?.gpu ? 'cyan' : 'default'}
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
            P2P Nodes
          </h2>

          {backend === 'online' && nodes.length === 0 && (
            <div className="rounded-xl border border-border bg-bg-elevated px-5 py-8 text-center">
              <p className="font-mono text-[12px] text-text-secondary">No nodes discovered yet</p>
              <p className="mt-1 font-mono text-[10px] text-text-dim">
                Start a node on the Network page to see it appear here
              </p>
            </div>
          )}

          {nodes.length > 0 && (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {nodes.map((node) => (
                <NodeCard key={node.peer_id} node={node} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
