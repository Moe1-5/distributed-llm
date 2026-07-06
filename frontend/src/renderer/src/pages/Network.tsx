/**
 * Network.tsx
 * Node and generator setup.
 *
 * Changes from previous version:
 *   - Model is now a dropdown from /models endpoint — no free text
 *   - Gated models show a HuggingFace redirect button if no token set
 *   - Layer range auto-fills based on selected model
 *   - Bootstrap peers auto-fill from server defaults if available
 *   - Generator start uses model's num_layers from server — no manual input
 */

import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { ModelInfo } from '../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Tab = 'serve' | 'inference'

interface ActivityEntry {
  id: string
  timestamp: Date
  message: string
  type: 'info' | 'success' | 'error'
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
}

function makeEntry(message: string, type: ActivityEntry['type']): ActivityEntry {
  return { id: makeId(), timestamp: new Date(), message, type }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Network(): React.JSX.Element {
  const [tab, setTab] = useState<Tab>('serve')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [defaultPeers, setDefaultPeers] = useState<string[]>([])
  const [tokenAvailable, setTokenAvailable] = useState(false)
  const [modelsLoading, setModelsLoading] = useState(true)
  const [activity, setActivity] = useState<ActivityEntry[]>([])

  // Serve Layers form state
  const [serveModel, setServeModel] = useState('')
  const [layerStart, setLayerStart] = useState(0)
  const [layerEnd, setLayerEnd] = useState(0)
  const [device, setDevice] = useState('cuda')
  const [servePeers, setServePeers] = useState('')
  const [nodeRunning, setNodeRunning] = useState(false)
  const [nodeLoading, setNodeLoading] = useState(false)

  // Run Inference form state
  const [inferModel, setInferModel] = useState('')
  const [inferPeers, setInferPeers] = useState('')
  const [genLoading, setGenLoading] = useState(false)
  const [genReady, setGenReady] = useState(false)

  // Status badges
  const [backendOk, setBackendOk] = useState(false)
  const [gpuAvailable, setGpuAvailable] = useState(false)

  // ---------------------------------------------------------------------------
  // Log helper
  // ---------------------------------------------------------------------------

  const log = useCallback((message: string, type: ActivityEntry['type'] = 'info') => {
    setActivity((prev) => [...prev.slice(-99), makeEntry(message, type)])
  }, [])

  // ---------------------------------------------------------------------------
  // Load models + status on mount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const loadInitial = async (): Promise<void> => {
      try {
        const [modelsRes, statusRes] = await Promise.all([api.getModels(), api.getStatus()])

        setModels(modelsRes.models)
        setDefaultPeers(modelsRes.default_peers)
        setTokenAvailable(modelsRes.token_available)

        // Auto-select first model
        if (modelsRes.models.length > 0) {
          const first = modelsRes.models[0]
          setServeModel(first.id)
          setInferModel(first.id)
          setLayerStart(0)
          setLayerEnd(first.num_layers)
        }

        // Auto-fill peers from server defaults
        if (modelsRes.default_peers.length > 0) {
          setServePeers(modelsRes.default_peers.join('\n'))
          setInferPeers(modelsRes.default_peers.join('\n'))
        }

        setBackendOk(true)
        setGpuAvailable(statusRes.gpu_available)
        setNodeRunning(statusRes.node_running)
        setGenReady(statusRes.generator_ready)
      } catch {
        setBackendOk(false)
      } finally {
        setModelsLoading(false)
      }
    }

    void loadInitial()
  }, [])

  // ---------------------------------------------------------------------------
  // When serve model changes, auto-update layer range
  // ---------------------------------------------------------------------------

  const handleServeModelChange = useCallback(
    (modelId: string) => {
      setServeModel(modelId)
      const model = models.find((m) => m.id === modelId)
      if (model) {
        setLayerStart(0)
        setLayerEnd(model.num_layers)
      }
    },
    [models]
  )

  // ---------------------------------------------------------------------------
  // Check if selected model needs a token
  // ---------------------------------------------------------------------------

  const selectedServeModel = models.find((m) => m.id === serveModel)
  const selectedInferModel = models.find((m) => m.id === inferModel)
  const serveNeedsToken = selectedServeModel?.gated && !tokenAvailable
  const inferNeedsToken = selectedInferModel?.gated && !tokenAvailable

  function openHuggingFace(): void {
    // Opens HuggingFace login in the user's browser
    window.open('https://huggingface.co/settings/tokens', '_blank')
  }

  // ---------------------------------------------------------------------------
  // Start node
  // ---------------------------------------------------------------------------

  const handleStartNode = useCallback(async () => {
    if (!serveModel || nodeLoading) return

    const peers = servePeers
      .split('\n')
      .map((p) => p.trim())
      .filter(Boolean)

    log(`Starting node: ${serveModel} layers ${layerStart}-${layerEnd}...`)
    setNodeLoading(true)

    try {
      const res = await api.startNode({
        model_name: serveModel,
        layer_start: layerStart,
        layer_end: layerEnd,
        dht_prefix: 'distribllm',
        initial_peers: peers,
        device
      })

      if (res.status === 'error') {
        if (res.error === 'gated_model_no_token') {
          log('Token required — opening HuggingFace...', 'error')
          openHuggingFace()
        } else {
          log(`Error: ${res.error}`, 'error')
        }
      } else if (res.status === 'started') {
        log(`Node started successfully`, 'success')
        if (res.info?.maddrs?.length) {
          log(`Address: ${res.info.maddrs[0]}`, 'info')
        }
        setNodeRunning(true)
      } else if (res.status === 'already_running') {
        log('Node already running', 'info')
        setNodeRunning(true)
      }
    } catch (err) {
      log(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    } finally {
      setNodeLoading(false)
    }
  }, [serveModel, layerStart, layerEnd, device, servePeers, nodeLoading, log])

  // ---------------------------------------------------------------------------
  // Stop node
  // ---------------------------------------------------------------------------

  const handleStopNode = useCallback(async () => {
    try {
      await api.stopNode()
      setNodeRunning(false)
      log('Node stopped', 'info')
    } catch (err) {
      log(`Stop failed: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    }
  }, [log])

  // ---------------------------------------------------------------------------
  // Start generator
  // ---------------------------------------------------------------------------

  const handleStartGenerator = useCallback(async () => {
    if (!inferModel || genLoading) return

    const peers = inferPeers
      .split('\n')
      .map((p) => p.trim())
      .filter(Boolean)

    log(`Starting generator: ${inferModel}...`)
    setGenLoading(true)

    try {
      const res = await api.startGenerator({
        model_name: inferModel,
        dht_prefix: 'distribllm',
        initial_peers: peers
      })

      if (res.status === 'error') {
        if (res.error === 'gated_model_no_token') {
          log('Token required — opening HuggingFace...', 'error')
          openHuggingFace()
        } else {
          log(`Error: ${res.error}`, 'error')
        }
      } else {
        log('Generator ready — go to Inference page', 'success')
        setGenReady(true)
      }
    } catch (err) {
      log(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    } finally {
      setGenLoading(false)
    }
  }, [inferModel, inferPeers, genLoading, log])

  // ---------------------------------------------------------------------------
  // Shared input styles
  // ---------------------------------------------------------------------------

  const inputCls = `
    w-full rounded-lg border border-border-bright bg-bg-surface
    px-3 py-2.5 text-[13px] text-text-primary outline-none
    placeholder:text-text-dim focus:border-cyan/40
    transition-colors duration-150 disabled:opacity-50
  `

  const labelCls = 'font-mono text-[10px] tracking-widest text-text-dim uppercase'

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left panel */}
      <div className="flex w-[480px] min-w-[480px] flex-col border-r border-border">
        {/* Header */}
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-6 py-5">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-text-primary">Network</h1>
            <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
              Serve local layers or prepare an inference client
            </p>
          </div>
          {/* Status badges */}
          <div className="flex items-center gap-1.5">
            {[
              { label: 'Backend', ok: backendOk },
              { label: 'Node', ok: nodeRunning },
              { label: 'Generator', ok: genReady },
              { label: 'GPU', ok: gpuAvailable }
            ].map((b) => (
              <span
                key={b.label}
                className={`rounded-full border px-2 py-0.5 font-mono text-[9px] font-semibold ${
                  b.ok
                    ? 'border-green/20 bg-green/5 text-green'
                    : 'border-border bg-bg-surface text-text-dim'
                }`}
              >
                {b.label}
              </span>
            ))}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex flex-shrink-0 gap-1 border-b border-border px-4 pt-3">
          {(
            [
              ['serve', 'Serve Layers'],
              ['inference', 'Run Inference']
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`
                  rounded-t-lg px-4 py-2 font-mono text-[11px] font-semibold
                  transition-all duration-150
                  ${
                    tab === id
                      ? 'border border-b-0 border-border bg-bg-elevated text-cyan'
                      : 'text-text-dim hover:text-text-secondary'
                  }
                `}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
          {/* ── SERVE LAYERS ── */}
          {tab === 'serve' && (
            <>
              <p className="text-[12px] leading-relaxed text-text-secondary">
                Serve a slice of transformer layers from this machine. Other clients will discover
                you via the DHT.
              </p>

              {/* Model dropdown */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Model</label>
                {modelsLoading ? (
                  <div className="h-10 animate-pulse rounded-lg bg-bg-surface" />
                ) : (
                  <select
                    value={serveModel}
                    onChange={(e) => handleServeModelChange(e.target.value)}
                    disabled={nodeRunning || nodeLoading}
                    className={inputCls}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id} — {m.description}
                      </option>
                    ))}
                  </select>
                )}

                {/* Gated model warning */}
                {serveNeedsToken && (
                  <div className="flex items-center justify-between rounded-lg border border-red/20 bg-red/5 px-3 py-2.5">
                    <p className="font-mono text-[11px] text-red">
                      Gated model — HuggingFace token required
                    </p>
                    <button
                      onClick={openHuggingFace}
                      className="ml-3 flex-shrink-0 rounded-lg border border-red/30 px-3 py-1 font-mono text-[10px] text-red hover:bg-red/10 transition-colors"
                    >
                      GET TOKEN ↗
                    </button>
                  </div>
                )}

                {/* Model info */}
                {selectedServeModel && (
                  <p className="font-mono text-[10px] text-text-dim">
                    {selectedServeModel.num_layers} layers total · {selectedServeModel.vram_gb}GB
                    VRAM · {selectedServeModel.gated ? 'gated' : 'open'}
                  </p>
                )}
              </div>

              {/* Layer range */}
              <div className="flex gap-3">
                <div className="flex flex-1 flex-col gap-1.5">
                  <label className={labelCls}>Layer Start</label>
                  <input
                    type="number"
                    value={layerStart}
                    min={0}
                    max={layerEnd - 1}
                    onChange={(e) => setLayerStart(Number(e.target.value))}
                    disabled={nodeRunning || nodeLoading}
                    className={inputCls}
                  />
                </div>
                <div className="flex flex-1 flex-col gap-1.5">
                  <label className={labelCls}>Layer End</label>
                  <input
                    type="number"
                    value={layerEnd}
                    min={layerStart + 1}
                    max={selectedServeModel?.num_layers ?? 999}
                    onChange={(e) => setLayerEnd(Number(e.target.value))}
                    disabled={nodeRunning || nodeLoading}
                    className={inputCls}
                  />
                </div>
              </div>

              {/* Device */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Device</label>
                <select
                  value={device}
                  onChange={(e) => setDevice(e.target.value)}
                  disabled={nodeRunning || nodeLoading}
                  className={inputCls}
                >
                  <option value="cuda">CUDA (GPU)</option>
                  <option value="cpu">CPU</option>
                </select>
              </div>

              {/* Bootstrap peers */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Bootstrap Peers (one per line)</label>
                <textarea
                  value={servePeers}
                  onChange={(e) => setServePeers(e.target.value)}
                  disabled={nodeRunning || nodeLoading}
                  placeholder="/ip4/1.2.3.4/tcp/7001/p2p/12D3KooW..."
                  rows={3}
                  className={`${inputCls} resize-none`}
                />
                {defaultPeers.length > 0 && (
                  <p className="font-mono text-[10px] text-green">
                    ✓ Default bootstrap peers loaded from server
                  </p>
                )}
              </div>

              {/* Start / Stop button */}
              {nodeRunning ? (
                <button
                  onClick={() => void handleStopNode()}
                  className="w-full rounded-xl border border-red/30 bg-red/10 py-3 font-mono text-[12px] font-semibold text-red transition-all hover:bg-red/20"
                >
                  STOP NODE
                </button>
              ) : (
                <button
                  onClick={() => void handleStartNode()}
                  disabled={nodeLoading || serveNeedsToken || !serveModel}
                  className={`
                    w-full rounded-xl border py-3 font-mono text-[12px] font-semibold
                    transition-all duration-150
                    ${
                      nodeLoading || serveNeedsToken || !serveModel
                        ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                        : 'cursor-pointer border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
                    }
                  `}
                >
                  {nodeLoading ? 'STARTING... (downloading model)' : 'START NODE'}
                </button>
              )}
            </>
          )}

          {/* ── RUN INFERENCE ── */}
          {tab === 'inference' && (
            <>
              <p className="text-[12px] leading-relaxed text-text-secondary">
                Connect to the network as a client. This downloads only the small local components
                (embeddings + LM head) and routes the transformer layers through the P2P network.
              </p>

              {/* Model dropdown */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Model</label>
                {modelsLoading ? (
                  <div className="h-10 animate-pulse rounded-lg bg-bg-surface" />
                ) : (
                  <select
                    value={inferModel}
                    onChange={(e) => setInferModel(e.target.value)}
                    disabled={genLoading || genReady}
                    className={inputCls}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id} — {m.runnable ? 'runnable' : 'not runnable'}
                      </option>
                    ))}
                  </select>
                )}

                {inferNeedsToken && (
                  <div className="flex items-center justify-between rounded-lg border border-red/20 bg-red/5 px-3 py-2.5">
                    <p className="font-mono text-[11px] text-red">
                      Gated model — HuggingFace token required
                    </p>
                    <button
                      onClick={openHuggingFace}
                      className="ml-3 flex-shrink-0 rounded-lg border border-red/30 px-3 py-1 font-mono text-[10px] text-red hover:bg-red/10 transition-colors"
                    >
                      GET TOKEN ↗
                    </button>
                  </div>
                )}

                {selectedInferModel && (
                  <div className="rounded-lg border border-border bg-bg-surface px-3 py-2.5">
                    <p
                      className={`font-mono text-[10px] ${
                        selectedInferModel.runnable ? 'text-green' : 'text-amber'
                      }`}
                    >
                      {selectedInferModel.runnable
                        ? `Runnable route across ${selectedInferModel.compatible_nodes} node(s)`
                        : `Not runnable: ${
                            selectedInferModel.route_reasons[0] ?? 'missing compatible coverage'
                          }`}
                    </p>
                    <p className="mt-1 font-mono text-[10px] text-text-dim">
                      {selectedInferModel.covered_layers} / {selectedInferModel.total_layers}{' '}
                      layers covered
                    </p>
                  </div>
                )}
              </div>

              {/* Bootstrap peers */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Bootstrap Peers (one per line)</label>
                <textarea
                  value={inferPeers}
                  onChange={(e) => setInferPeers(e.target.value)}
                  disabled={genLoading || genReady}
                  placeholder="/ip4/1.2.3.4/tcp/7001/p2p/12D3KooW..."
                  rows={3}
                  className={`${inputCls} resize-none`}
                />
              </div>

              {genReady ? (
                <div className="rounded-xl border border-green/20 bg-green/5 px-4 py-3">
                  <p className="font-mono text-[12px] text-green">
                    ✓ Generator ready — go to Inference page to start chatting
                  </p>
                </div>
              ) : (
                <button
                  onClick={() => void handleStartGenerator()}
                  disabled={genLoading || inferNeedsToken || !inferModel}
                  className={`
                    w-full rounded-xl border py-3 font-mono text-[12px] font-semibold
                    transition-all duration-150
                    ${
                      genLoading || inferNeedsToken || !inferModel
                        ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                        : 'cursor-pointer border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
                    }
                  `}
                >
                  {genLoading ? 'CONNECTING...' : 'START GENERATOR'}
                </button>
              )}
            </>
          )}

        </div>
      </div>

      {/* Activity log */}
      <div className="flex flex-1 flex-col">
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-5 py-3.5">
          <span className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
            Activity Log
          </span>
          <button
            onClick={() => setActivity([])}
            className="font-mono text-[10px] text-text-dim hover:text-text-secondary transition-colors"
          >
            CLEAR
          </button>
        </div>
        <div className="flex flex-1 flex-col gap-1.5 overflow-y-auto p-4">
          {activity.length === 0 ? (
            <p className="font-mono text-[11px] text-text-dim">No activity yet.</p>
          ) : (
            activity.map((entry) => (
              <div key={entry.id} className="flex items-start gap-3">
                <span className="flex-shrink-0 font-mono text-[9px] text-text-dim mt-0.5">
                  {entry.timestamp.toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit'
                  })}
                </span>
                <span
                  className={`font-mono text-[11px] leading-relaxed ${
                    entry.type === 'error'
                      ? 'text-red'
                      : entry.type === 'success'
                        ? 'text-green'
                        : 'text-text-secondary'
                  }`}
                >
                  {entry.message}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
