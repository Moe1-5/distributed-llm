/**
 * Network.tsx
 * Node and generator setup.
 *
 * Changes from previous version:
 *   - Model is now a dropdown from /models endpoint — no free text
 *   - Gated models use validated local model directory import
 *   - Layer range auto-fills based on selected model
 *   - Generator start uses model's num_layers from server — no manual input
 */

import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type {
  HuggingFaceConnection,
  HuggingFaceDeviceFlow,
  HuggingFaceDownloadJob,
  LocalModelImport,
  ModelInfo
} from '../api/client'

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

function tuningLabel(model: ModelInfo): string {
  return model.tuning === 'base' ? 'base' : model.tuning === 'chat' ? 'chat' : 'instruct'
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Network(): React.JSX.Element {
  const [tab, setTab] = useState<Tab>('serve')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [localImports, setLocalImports] = useState<LocalModelImport[]>([])
  const [modelsLoading, setModelsLoading] = useState(true)
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [localImportPath, setLocalImportPath] = useState('')
  const [localImportLoading, setLocalImportLoading] = useState(false)
  const [localImportError, setLocalImportError] = useState<string | null>(null)
  const [hfConnection, setHfConnection] = useState<HuggingFaceConnection | null>(null)
  const [hfFlow, setHfFlow] = useState<HuggingFaceDeviceFlow | null>(null)
  const [hfAuthLoading, setHfAuthLoading] = useState(false)
  const [hfPolling, setHfPolling] = useState(false)
  const [hfDownloadJob, setHfDownloadJob] = useState<HuggingFaceDownloadJob | null>(null)

  // Serve Layers form state
  const [serveModel, setServeModel] = useState('')
  const [layerStart, setLayerStart] = useState(0)
  const [layerEnd, setLayerEnd] = useState(0)
  const [device, setDevice] = useState('cuda')
  const [nodeRunning, setNodeRunning] = useState(false)
  const [nodeLoading, setNodeLoading] = useState(false)

  // Run Inference form state
  const [inferModel, setInferModel] = useState('')
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
        const [modelsRes, statusRes, hfRes] = await Promise.all([
          api.getModels(),
          api.getStatus(),
          api.getHuggingFaceConnection()
        ])

        setModels(modelsRes.models)
        setLocalImports(statusRes.local_models ?? [])
        setHfConnection(hfRes)

        // Auto-select first model
        if (modelsRes.models.length > 0) {
          const first = modelsRes.models[0]
          setServeModel(first.id)
          setInferModel(first.id)
          setLayerStart(0)
          setLayerEnd(first.num_layers)
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
  // Local gated-model import
  // ---------------------------------------------------------------------------

  const selectedServeModel = models.find((m) => m.id === serveModel)
  const selectedInferModel = models.find((m) => m.id === inferModel)
  const serveNeedsLocalImport = Boolean(selectedServeModel?.gated && !selectedServeModel.local_imported)
  const inferNeedsLocalImport = Boolean(selectedInferModel?.gated && !selectedInferModel.local_imported)
  const selectedModelForImport = tab === 'serve' ? serveModel : inferModel

  const markLocalImport = useCallback((imported: LocalModelImport) => {
    setLocalImports((prev) => [
      ...prev.filter((item) => item.model_name !== imported.model_name),
      imported
    ])
    setModels((prev) =>
      prev.map((model) =>
        model.id === imported.model_name
          ? { ...model, available: true, local_imported: true, local_import: imported }
          : model
      )
    )
  }, [])

  const refreshModelsAndImports = useCallback(async () => {
    const [modelsRes, localRes] = await Promise.all([api.getModels(), api.getLocalModels()])
    setModels(modelsRes.models)
    setLocalImports(localRes.models)
  }, [])

  const ensureLocalImport = useCallback(
    async (modelId: string): Promise<boolean> => {
      const model = models.find((m) => m.id === modelId)
      if (!model?.gated) return true
      if (model.local_imported) return true
      log(`Import a local approved model directory for ${modelId} before starting.`, 'error')
      return false
    },
    [log, models]
  )

  const handleImportLocalModel = useCallback(async () => {
    const modelId = selectedModelForImport
    const path = localImportPath.trim()
    if (!modelId || !path || localImportLoading) return
    const loweredPath = path.toLowerCase()
    if (
      loweredPath.startsWith('http://') ||
      loweredPath.startsWith('https://') ||
      loweredPath.startsWith('huggingface.co/')
    ) {
      const message =
        'Enter the local folder path after downloading the approved model files, not the Hugging Face URL.'
      setLocalImportError(message)
      log(message, 'error')
      return
    }

    setLocalImportLoading(true)
    setLocalImportError(null)
    log(`Validating local model directory for ${modelId}...`)
    try {
      const inspection = await api.inspectLocalModel(modelId, path)
      if (!inspection.valid) {
        const message = inspection.message ?? inspection.error ?? 'Local model directory is invalid'
        setLocalImportError(message)
        log(message, 'error')
        return
      }
      const imported = await api.importLocalModel(modelId, path)
      markLocalImport(imported.model)
      setLocalImportPath('')
      log(`Imported local model directory for ${modelId}`, 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to import local model'
      setLocalImportError(message)
      log(message, 'error')
    } finally {
      setLocalImportLoading(false)
    }
  }, [localImportLoading, localImportPath, log, markLocalImport, selectedModelForImport])

  const pollHuggingFaceLogin = useCallback(
    async (flowId: string, intervalSeconds: number): Promise<void> => {
      setHfPolling(true)
      try {
        const result = await api.pollHuggingFaceDeviceLogin(flowId)
        if (result.status === 'connected' && result.connection) {
          setHfConnection(result.connection)
          setHfFlow(null)
          log('Hugging Face connected. Approved gated models can now be downloaded.', 'success')
          return
        }

        const nextInterval = result.interval ?? intervalSeconds
        window.setTimeout(() => {
          void pollHuggingFaceLogin(flowId, nextInterval)
        }, Math.max(2, nextInterval) * 1000)
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Hugging Face login failed'
        setLocalImportError(message)
        setHfFlow(null)
        log(message, 'error')
      } finally {
        setHfPolling(false)
      }
    },
    [log]
  )

  const handleConnectHuggingFace = useCallback(async () => {
    if (hfAuthLoading) return
    setHfAuthLoading(true)
    setLocalImportError(null)
    try {
      const flow = await api.startHuggingFaceDeviceLogin()
      setHfFlow(flow)
      const url = flow.verification_uri_complete ?? flow.verification_uri
      const opened = await window.api.openExternalUrl(url)
      log(
        opened
          ? `Opened Hugging Face authorization. Enter code ${flow.user_code}.`
          : `Open ${url} manually and enter code ${flow.user_code}.`,
        'info'
      )
      window.setTimeout(() => {
        void pollHuggingFaceLogin(flow.flow_id, flow.interval)
      }, Math.max(2, flow.interval) * 1000)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not start Hugging Face login'
      setLocalImportError(message)
      log(message, 'error')
    } finally {
      setHfAuthLoading(false)
    }
  }, [hfAuthLoading, log, pollHuggingFaceLogin])

  const handleDisconnectHuggingFace = useCallback(async () => {
    if (hfAuthLoading) return
    setHfAuthLoading(true)
    setLocalImportError(null)
    try {
      const result = await api.disconnectHuggingFace()
      setHfConnection(result.connection)
      setHfFlow(null)
      log('Hugging Face disconnected. Existing local model imports still work offline.', 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not disconnect Hugging Face'
      setLocalImportError(message)
      log(message, 'error')
    } finally {
      setHfAuthLoading(false)
    }
  }, [hfAuthLoading, log])

  const pollHuggingFaceDownload = useCallback(
    async (jobId: string): Promise<void> => {
      try {
        const result = await api.getHuggingFaceDownload(jobId)
        setHfDownloadJob(result.job)

        if (result.job.status === 'completed' && result.job.model) {
          markLocalImport(result.job.model)
          await refreshModelsAndImports()
          log(result.job.message ?? `Downloaded ${result.job.model_name}`, 'success')
          setHfDownloadJob(null)
          return
        }

        if (result.job.status === 'failed') {
          const message = result.job.message ?? 'Hugging Face download failed'
          setLocalImportError(message)
          log(message, 'error')
          setHfDownloadJob(null)
          return
        }

        if (result.job.status === 'cancelled') {
          log(result.job.message ?? 'Hugging Face download cancelled', 'info')
          setHfDownloadJob(null)
          return
        }

        window.setTimeout(() => {
          void pollHuggingFaceDownload(jobId)
        }, 1500)
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Could not read download status'
        setLocalImportError(message)
        log(message, 'error')
        setHfDownloadJob(null)
      }
    },
    [log, markLocalImport, refreshModelsAndImports]
  )

  const handleDownloadHuggingFaceModel = useCallback(
    async (modelId: string) => {
      if (!modelId || hfDownloadJob) return
      setLocalImportError(null)
      log(`Starting Hugging Face download for ${modelId}...`)
      try {
        const result = await api.downloadHuggingFaceModel(modelId)
        setHfDownloadJob(result.job)
        void pollHuggingFaceDownload(result.job.job_id)
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to start model download'
        setLocalImportError(message)
        log(message, 'error')
        setHfDownloadJob(null)
      }
    },
    [hfDownloadJob, log, pollHuggingFaceDownload]
  )

  const handleCancelHuggingFaceDownload = useCallback(async () => {
    if (!hfDownloadJob) return
    try {
      const result = await api.cancelHuggingFaceDownload(hfDownloadJob.job_id)
      setHfDownloadJob(result.job)
      log(result.job.message ?? 'Cancel requested for Hugging Face download.', 'info')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not cancel download'
      setLocalImportError(message)
      log(message, 'error')
    }
  }, [hfDownloadJob, log])

  const handleBrowseLocalModelDirectory = useCallback(async () => {
    if (localImportLoading) return
    try {
      const selectedPath = await window.api.selectLocalModelDirectory()
      if (selectedPath) {
        setLocalImportPath(selectedPath)
        setLocalImportError(null)
        log('Selected local model directory.', 'info')
      }
    } catch (err) {
      log(
        err instanceof Error
          ? `Could not open folder picker: ${err.message}`
          : 'Could not open folder picker. Paste the local model path instead.',
        'error'
      )
    }
  }, [localImportLoading, log])

  const handleRemoveLocalModel = useCallback(async () => {
    const modelId = selectedModelForImport
    if (!modelId || localImportLoading) return
    const confirmed = window.confirm(
      `Remove ${modelId} from DistribLLM and delete its downloaded Hugging Face cache snapshot when possible?`
    )
    if (!confirmed) return

    setLocalImportLoading(true)
    setLocalImportError(null)
    try {
      const result = await api.removeLocalModel(modelId, true)
      setLocalImports((prev) => prev.filter((item) => item.model_name !== modelId))
      setModels((prev) =>
        prev.map((model) =>
          model.id === modelId
            ? { ...model, available: !model.gated, local_imported: false, local_import: undefined }
            : model
        )
      )
      log(
        result.files_deleted
          ? `Deleted ${modelId} cached files (${result.deleted_size})`
          : result.message,
        result.files_deleted ? 'success' : 'info'
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to remove local model import'
      setLocalImportError(message)
      log(message, 'error')
    } finally {
      setLocalImportLoading(false)
    }
  }, [localImportLoading, log, selectedModelForImport])

  useEffect(() => {
    if (localImports.length === 0) return
    setModels((prev) =>
      prev.map((model) => {
        const imported = localImports.find((item) => item.model_name === model.id)
        return imported
          ? { ...model, available: true, local_imported: true, local_import: imported }
          : model
      })
    )
  }, [localImports])

  // ---------------------------------------------------------------------------
  // Start node
  // ---------------------------------------------------------------------------

  const handleStartNode = useCallback(async () => {
    if (!serveModel || nodeLoading) return
    if (serveNeedsLocalImport) {
      log(`Import a local approved model directory for ${serveModel} before starting.`, 'error')
      return
    }

    log(`Starting node: ${serveModel} layers ${layerStart}-${layerEnd}...`)
    setNodeLoading(true)

    try {
      const accessOk = await ensureLocalImport(serveModel)
      if (!accessOk) return

      const res = await api.startNode({
        model_name: serveModel,
        layer_start: layerStart,
        layer_end: layerEnd,
        dht_prefix: 'distribllm',
        initial_peers: [],
        device
      })

      if (res.status === 'error') {
        if (res.error === 'local_model_import_required') {
          log(res.message ?? 'Local model import required', 'error')
        } else {
          log(`Error: ${res.message ?? res.error}`, 'error')
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
  }, [
    serveModel,
    nodeLoading,
    serveNeedsLocalImport,
    log,
    layerStart,
    layerEnd,
    device,
    ensureLocalImport
  ])

  // ---------------------------------------------------------------------------
  // Start generator
  // ---------------------------------------------------------------------------

  const handleStartGenerator = useCallback(async () => {
    if (!inferModel || genLoading) return
    if (inferNeedsLocalImport) {
      log(`Import a local approved model directory for ${inferModel} before starting.`, 'error')
      return
    }

    log(`Starting generator: ${inferModel}...`)
    setGenLoading(true)

    try {
      const accessOk = await ensureLocalImport(inferModel)
      if (!accessOk) return

      const res = await api.startGenerator({
        model_name: inferModel,
        dht_prefix: 'distribllm',
        initial_peers: []
      })

      if (res.status === 'error') {
        if (res.error === 'local_model_import_required') {
          log(res.message ?? 'Local model import required', 'error')
        } else {
          log(`Error: ${res.message ?? res.error}`, 'error')
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
  }, [inferModel, genLoading, inferNeedsLocalImport, log, ensureLocalImport])

  const handleStopGenerator = useCallback(async () => {
    if (!genReady || genLoading) return

    log('Stopping generator...')
    setGenLoading(true)

    try {
      const res = await api.stopGenerator()
      if (res.status === 'stop_requested') {
        log('Generator stop requested', 'success')
      } else {
        log(`Generator status: ${res.status}`, 'info')
      }
      setGenReady(false)
    } catch (err) {
      log(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    } finally {
      setGenLoading(false)
    }
  }, [genLoading, genReady, log])

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

  const renderLocalImportPanel = (model: ModelInfo | undefined): React.JSX.Element | null => {
    if (!model?.gated) return null
    const activeDownloadForModel = hfDownloadJob?.model_name === model.id ? hfDownloadJob : null

    if (model.local_imported) {
      return (
        <div className="rounded-lg border border-green/20 bg-green/5 px-3 py-2.5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] text-green">LOCAL MODEL IMPORTED</p>
              <p className="mt-1 font-mono text-[10px] text-text-dim">
                {model.local_import?.weight_file_count ?? 0} weight file(s) validated for offline
                loading
              </p>
            </div>
              <button
                onClick={() => void handleRemoveLocalModel()}
                disabled={localImportLoading}
                className="rounded border border-red/30 bg-red/5 px-2.5 py-1.5 font-mono text-[10px] text-red hover:bg-red/10 disabled:opacity-50"
              >
                DELETE FILES
              </button>
          </div>
        </div>
      )
    }

    return (
      <div className="flex flex-col gap-3 rounded-lg border border-amber/20 bg-amber/5 px-3 py-3">
        <div>
          <p className="font-mono text-[10px] text-amber">HUGGING FACE ACCESS REQUIRED</p>
          <p className="mt-1 text-[12px] leading-relaxed text-text-secondary">
            Accept this model's Hugging Face terms first. Then connect your account so DistribLLM
            can download the approved files locally.
          </p>
        </div>

        <div className="rounded-lg border border-border bg-bg-surface px-3 py-2.5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p
                className={`font-mono text-[10px] ${
                  hfConnection?.connected ? 'text-green' : 'text-text-dim'
                }`}
              >
                {hfConnection?.connected ? 'HUGGING FACE CONNECTED' : 'HUGGING FACE NOT CONNECTED'}
              </p>
              <p className="mt-1 font-mono text-[10px] text-text-dim">
                {hfConnection?.connected
                  ? hfConnection.username
                    ? `Signed in as ${hfConnection.username}`
                    : 'Authorized for gated downloads'
                  : hfConnection?.configured === false
                    ? 'OAuth client ID is not configured'
                    : 'Authorize in your browser; no token paste needed'}
              </p>
            </div>
            {hfConnection?.connected ? (
              <button
                onClick={() => void handleDisconnectHuggingFace()}
                disabled={hfAuthLoading}
                className="rounded border border-border px-2.5 py-1.5 font-mono text-[10px] text-text-secondary hover:bg-bg-hover disabled:opacity-50"
              >
                DISCONNECT
              </button>
            ) : (
              <button
                onClick={() => void handleConnectHuggingFace()}
                disabled={hfAuthLoading || hfConnection?.configured === false}
                className="rounded border border-cyan/30 bg-cyan-dim px-2.5 py-1.5 font-mono text-[10px] text-cyan hover:bg-cyan/20 disabled:cursor-not-allowed disabled:border-border disabled:bg-bg-surface disabled:text-text-dim"
              >
                {hfAuthLoading ? 'OPENING...' : 'CONNECT'}
              </button>
            )}
          </div>

          {hfFlow && (
            <div className="mt-3 rounded border border-cyan/20 bg-cyan/5 px-3 py-2">
              <p className="font-mono text-[10px] text-cyan">AUTH CODE {hfFlow.user_code}</p>
              <p className="mt-1 font-mono text-[10px] text-text-dim">
                Browser authorization is waiting{hfPolling ? ' for approval' : ''}.
              </p>
              <p className="mt-1 break-all font-mono text-[10px] text-text-secondary">
                Open {hfFlow.verification_uri_complete ?? hfFlow.verification_uri}
              </p>
            </div>
          )}
        </div>

        <button
          onClick={() => void handleDownloadHuggingFaceModel(model.id)}
          disabled={!hfConnection?.connected || hfDownloadJob !== null}
          className={`
            h-10 rounded-lg border font-mono text-[11px] font-semibold transition-colors
            ${
              !hfConnection?.connected || hfDownloadJob !== null
                ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                : 'cursor-pointer border-green/30 bg-green/10 text-green hover:bg-green/20'
            }
          `}
        >
          {activeDownloadForModel ? activeDownloadForModel.status.toUpperCase() : 'DOWNLOAD APPROVED MODEL'}
        </button>

        {activeDownloadForModel && (
          <div className="rounded-lg border border-cyan/20 bg-cyan/5 px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <p className="font-mono text-[10px] text-cyan">
                {activeDownloadForModel.message ?? 'Downloading from Hugging Face.'}
              </p>
              <button
                onClick={() => void handleCancelHuggingFaceDownload()}
                disabled={activeDownloadForModel.cancel_requested}
                className="rounded border border-border px-2 py-1 font-mono text-[9px] text-text-secondary hover:bg-bg-hover disabled:opacity-50"
              >
                {activeDownloadForModel.cancel_requested ? 'CANCEL SENT' : 'CANCEL'}
              </button>
            </div>
          </div>
        )}

        <div className="border-t border-border pt-3">
          <p className="mb-2 font-mono text-[10px] text-text-dim">
            Already downloaded it yourself? Import the folder instead.
          </p>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={localImportPath}
            onChange={(e) => setLocalImportPath(e.target.value)}
            placeholder="/path/to/downloaded/model"
            disabled={localImportLoading}
            className={inputCls}
          />
          <button
            onClick={() => void handleBrowseLocalModelDirectory()}
            disabled={localImportLoading}
            className="h-10 rounded-lg border border-border px-3 font-mono text-[11px] text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-50"
          >
            BROWSE
          </button>
        </div>
        {localImportError && (
          <div className="rounded-lg border border-red/20 bg-red/5 px-3 py-2">
            <p className="font-mono text-[11px] text-red">{localImportError}</p>
          </div>
        )}
        <div className="flex gap-2">
          <button
            onClick={() => void handleImportLocalModel()}
            disabled={!localImportPath.trim() || localImportLoading}
            className={`
              h-10 flex-1 rounded-lg border font-mono text-[11px] font-semibold transition-colors
              ${
                !localImportPath.trim() || localImportLoading
                  ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                  : 'cursor-pointer border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
              }
            `}
          >
            {localImportLoading ? 'VALIDATING...' : 'IMPORT LOCAL MODEL'}
          </button>
        </div>
      </div>
    )
  }

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
              {/* Model dropdown */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Model</label>
                {modelsLoading ? (
                  <div className="h-10 animate-pulse rounded-lg bg-bg-surface" />
                ) : (
                  <select
                    value={serveModel}
                    onChange={(e) => handleServeModelChange(e.target.value)}
                    disabled={nodeLoading}
                    className={inputCls}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id} — {tuningLabel(m)} — {m.description}
                      </option>
                    ))}
                  </select>
                )}

                {/* Model info */}
                {selectedServeModel && (
                  <p className="font-mono text-[10px] text-text-dim">
                    {selectedServeModel.num_layers} layers total · {selectedServeModel.vram_gb}GB
                    VRAM · {selectedServeModel.gated ? 'gated' : 'open'} ·{' '}
                    {tuningLabel(selectedServeModel)}
                  </p>
                )}
              </div>

              {renderLocalImportPanel(selectedServeModel)}

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
                    disabled={nodeLoading}
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
                    disabled={nodeLoading}
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
                  disabled={nodeLoading}
                  className={inputCls}
                >
                  <option value="cuda">CUDA (GPU)</option>
                  <option value="cpu">CPU</option>
                </select>
              </div>

              <button
                onClick={() => void handleStartNode()}
                disabled={nodeLoading || !serveModel}
                className={`
                  w-full rounded-xl border py-3 font-mono text-[12px] font-semibold
                  transition-all duration-150
                  ${
                    nodeLoading || !serveModel
                      ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                      : 'cursor-pointer border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
                  }
                `}
              >
                {nodeLoading ? 'STARTING...' : 'START NODE'}
              </button>
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
                        {m.id} — {tuningLabel(m)}
                      </option>
                    ))}
                  </select>
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

              {renderLocalImportPanel(selectedInferModel)}

              {genReady ? (
                <div className="flex flex-col gap-3 rounded-xl border border-green/20 bg-green/5 px-4 py-3">
                  <div>
                    <p className="font-mono text-[12px] text-green">
                      ✓ Generator ready — go to Inference page to start chatting
                    </p>
                    <p className="mt-1 text-[12px] text-text-secondary">
                      Stop the generator when changing models or freeing local components.
                    </p>
                  </div>
                  <button
                    onClick={() => void handleStopGenerator()}
                    disabled={genLoading}
                    className={`
                      w-full rounded-lg border py-2.5 font-mono text-[11px] font-semibold
                      transition-all duration-150
                      ${
                        genLoading
                          ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                          : 'cursor-pointer border-red/30 bg-red/10 text-red hover:bg-red/20'
                      }
                    `}
                  >
                    {genLoading ? 'STOPPING...' : 'STOP GENERATOR'}
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => void handleStartGenerator()}
                  disabled={genLoading || !inferModel}
                  className={`
                    w-full rounded-xl border py-3 font-mono text-[12px] font-semibold
                    transition-all duration-150
                    ${
                      genLoading || !inferModel
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
