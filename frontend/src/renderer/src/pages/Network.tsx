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

import React, { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { recordDiagnostic } from '../api/diagnostics'
import { applyIndependently } from '../api/independentRefresh'
import { modelPresentation } from '../api/presentationState'
import { isServingPlanAuthoritative, servingPlanStatus } from '../api/servingPlanState'
import type {
  HuggingFaceConnection,
  HuggingFaceDeviceFlow,
  HuggingFaceDownloadJob,
  GeneratorStatus,
  LifecycleJob,
  LocalModelImport,
  ModelInfo,
  ServingPlan
} from '../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Tab = 'serve' | 'inference'
type ServingMode = 'recommended' | 'custom'

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

function formatRanges(ranges: Array<{ start: number; end: number }>): string {
  return ranges.length > 0
    ? ranges.map((range) => `${range.start}-${range.end}`).join(', ')
    : 'none'
}

interface StartResult {
  status: string
  info?: { maddrs?: string[] }
  error?: string
  message?: string
  plan?: ServingPlan
  requires_generator_stop?: boolean
}

async function waitForLifecycleJob(
  initial: LifecycleJob,
  onUpdate: (job: LifecycleJob) => void
): Promise<LifecycleJob> {
  let current = initial
  onUpdate(current)
  while (current.status === 'queued' || current.status === 'running') {
    await new Promise((resolve) => window.setTimeout(resolve, 500))
    current = await api.getLifecycleJob(current.job_id)
    onUpdate(current)
  }
  return current
}

function startResultFromJob(job: LifecycleJob): StartResult {
  const candidate = job.result
  if (candidate && typeof candidate.status === 'string') {
    const info = candidate.info
    const infoRecord = info && typeof info === 'object' ? (info as Record<string, unknown>) : null
    const plan = candidate.plan
    return {
      status: candidate.status,
      error: typeof candidate.error === 'string' ? candidate.error : undefined,
      message: typeof candidate.message === 'string' ? candidate.message : undefined,
      info: infoRecord
        ? {
            maddrs: Array.isArray(infoRecord.maddrs)
              ? infoRecord.maddrs.filter((item): item is string => typeof item === 'string')
              : undefined
          }
        : undefined,
      plan: plan && typeof plan === 'object' ? (plan as ServingPlan) : undefined
    }
  }
  return {
    status: job.status === 'failed' ? 'error' : job.status,
    error: job.error ?? undefined
  }
}

function CoverageStrip({
  plan,
  showRecommendation
}: {
  plan: ServingPlan
  showRecommendation: boolean
}): React.JSX.Element {
  return (
    <div className="flex h-8 w-full overflow-hidden rounded border border-border bg-bg-surface">
      {plan.segments.map((segment) => {
        const width = ((segment.end - segment.start) / plan.total_layers) * 100
        const color =
          segment.status === 'missing'
            ? 'bg-red/25'
            : segment.status === 'redundant'
              ? 'bg-amber/30'
              : 'bg-green/25'
        return (
          <div
            key={`${segment.start}-${segment.end}`}
            title={`Layers ${segment.start}-${segment.end}: ${segment.provider_count} provider(s)${
              showRecommendation && segment.recommended ? '; recommended' : ''
            }`}
            className={`h-full border-r border-bg-base/60 last:border-r-0 ${color} ${
              showRecommendation && segment.recommended
                ? 'shadow-[inset_0_0_0_2px_rgba(0,212,255,0.85)]'
                : ''
            }`}
            style={{ width: `${width}%` }}
          />
        )
      })}
    </div>
  )
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
  const [layerCount, setLayerCount] = useState(1)
  const [servingMode, setServingMode] = useState<ServingMode>('recommended')
  const [servingPlan, setServingPlan] = useState<ServingPlan | null>(null)
  const [servingPlanLoading, setServingPlanLoading] = useState(false)
  const [servingPlanError, setServingPlanError] = useState<string | null>(null)
  const [servingPlanReceivedAt, setServingPlanReceivedAt] = useState<Date | null>(null)
  const [device, setDevice] = useState('cuda')
  const [nodeRunning, setNodeRunning] = useState(false)
  const [nodeLoading, setNodeLoading] = useState(false)
  const [nodeProgress, setNodeProgress] = useState('starting')
  const [nodeJobId, setNodeJobId] = useState<string | null>(null)

  // Run Inference form state
  const [inferModel, setInferModel] = useState('')
  const [inferencePlan, setInferencePlan] = useState<ServingPlan | null>(null)
  const [genLoading, setGenLoading] = useState(false)
  const [genProgress, setGenProgress] = useState('starting')
  const [genJobId, setGenJobId] = useState<string | null>(null)
  const [genReady, setGenReady] = useState(false)
  const [generatorStatus, setGeneratorStatus] = useState<GeneratorStatus | null>(null)
  const lastGeneratorDiagnosticRef = useRef<string | null>(null)
  const servingPlanPromiseRef = useRef<Promise<ServingPlan | null> | null>(null)
  const inferencePlanPromiseRef = useRef<Promise<void> | null>(null)
  const generatorStatusPromiseRef = useRef<Promise<void> | null>(null)
  const lastServingPlanStateRef = useRef<string | null>(null)
  const modelAvailabilityPromiseRef = useRef<Promise<void> | null>(null)

  // Status badges
  const [backendOk, setBackendOk] = useState(false)
  const [gpuAvailable, setGpuAvailable] = useState(false)

  // ---------------------------------------------------------------------------
  // Log helper
  // ---------------------------------------------------------------------------

  const log = useCallback((message: string, type: ActivityEntry['type'] = 'info') => {
    setActivity((prev) => [...prev.slice(-99), makeEntry(message, type)])
    recordDiagnostic({
      source: 'network',
      severity: type,
      summary: message
    })
  }, [])

  useEffect(() => {
    let active = true
    const refreshGeneratorStatus = async (): Promise<void> => {
      if (generatorStatusPromiseRef.current) return generatorStatusPromiseRef.current
      const request = api
        .getGeneratorStatus()
        .then((status) => {
          if (active) {
            const diagnostic = JSON.stringify({
              state: status.state,
              ready: status.ready,
              routeReady: status.route_ready,
              healthRevision: status.health?.health_revision ?? null,
              reasons: status.reasons
            })
            if (
              lastGeneratorDiagnosticRef.current !== null &&
              lastGeneratorDiagnosticRef.current !== diagnostic
            ) {
              const health = status.health
              const context = [
                `state=${status.state}`,
                `route=${status.route_ready ? 'ready' : 'unavailable'}`,
                health?.health_revision ? `health=${health.health_revision}` : null,
                health?.active_probes !== undefined
                  ? `active_probes=${health.active_probes}`
                  : null,
                health?.last_discovery_error
                  ? `discovery_error=${health.last_discovery_error}`
                  : null,
                status.reasons.length ? `reason=${status.reasons.join('; ')}` : null
              ]
                .filter(Boolean)
                .join(' | ')
              log(
                `Generator runtime changed: ${context}`,
                status.ready ? 'success' : status.state === 'suspended' ? 'error' : 'info'
              )
            }
            lastGeneratorDiagnosticRef.current = diagnostic
            setGenReady(status.ready)
            setGeneratorStatus(status)
          }
        })
        .catch(() => {
          if (active) {
            setGenReady(false)
            setGeneratorStatus(null)
          }
        })
        .finally(() => {
          if (generatorStatusPromiseRef.current === request) {
            generatorStatusPromiseRef.current = null
          }
        })
      generatorStatusPromiseRef.current = request
      return request
    }
    void refreshGeneratorStatus()
    const interval = window.setInterval(() => void refreshGeneratorStatus(), 2_500)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [log])

  // ---------------------------------------------------------------------------
  // Load models + status on mount
  // ---------------------------------------------------------------------------

  const refreshModelAvailability = useCallback(async (): Promise<void> => {
    if (modelAvailabilityPromiseRef.current) return modelAvailabilityPromiseRef.current
    const request = api
      .getModels()
      .then((result) => setModels(result.models))
      .catch((error) => {
        recordDiagnostic({
          source: 'network',
          severity: 'warning',
          summary: 'Full model availability refresh failed; retaining the stable catalog.',
          details: { error: error instanceof Error ? error.message : String(error) }
        })
      })
      .finally(() => {
        if (modelAvailabilityPromiseRef.current === request) {
          modelAvailabilityPromiseRef.current = null
        }
      })
    modelAvailabilityPromiseRef.current = request
    return request
  }, [])

  useEffect(() => {
    let active = true
    const loadInitial = async (): Promise<void> => {
      const modelsRequest = applyIndependently(
        api.getModelCatalog(),
        (result) => {
          if (!active) return
          setModels(result.models)
          setModelsLoading(false)
          if (result.models.length > 0) {
            const first = result.models[0]
            setServeModel(first.id)
            setInferModel(first.id)
            const initialLayerCount = Math.max(1, Math.ceil(first.num_layers / 2))
            setLayerCount(initialLayerCount)
            setLayerStart(0)
            setLayerEnd(initialLayerCount)
          }
        },
        () => {
          if (active) setModelsLoading(false)
        }
      )
      const statusRequest = applyIndependently(
        api.getStatus(),
        (status) => {
          if (!active) return
          setLocalImports(status.local_models ?? [])
          setBackendOk(true)
          setGpuAvailable(status.gpu_available)
          setNodeRunning(status.node_running)
          setGenReady(status.generator_ready)
        },
        () => {
          if (active) setBackendOk(false)
        }
      )
      const hfRequest = applyIndependently(api.getHuggingFaceConnection(), (connection) => {
        if (active) setHfConnection(connection)
      })
      await Promise.allSettled([modelsRequest, statusRequest, hfRequest])
      if (active) void refreshModelAvailability()
    }

    void loadInitial()
    return () => {
      active = false
    }
  }, [refreshModelAvailability])

  useEffect(() => {
    const interval = window.setInterval(() => void refreshModelAvailability(), 10_000)
    return () => window.clearInterval(interval)
  }, [refreshModelAvailability])

  // ---------------------------------------------------------------------------
  // When serve model changes, auto-update layer range
  // ---------------------------------------------------------------------------

  const handleServeModelChange = useCallback(
    (modelId: string) => {
      setServeModel(modelId)
      const model = models.find((m) => m.id === modelId)
      if (model) {
        const nextLayerCount = Math.max(1, Math.ceil(model.num_layers / 2))
        setLayerCount(nextLayerCount)
        setLayerStart(0)
        setLayerEnd(nextLayerCount)
        setServingPlan(null)
        setServingPlanError(null)
        setServingPlanReceivedAt(null)
      }
    },
    [models]
  )

  // ---------------------------------------------------------------------------
  // Local gated-model import
  // ---------------------------------------------------------------------------

  const selectedServeModel = models.find((m) => m.id === serveModel)
  const selectedInferModel = models.find((m) => m.id === inferModel)
  const serveNeedsLocalImport = Boolean(
    selectedServeModel?.gated && !selectedServeModel.local_imported
  )
  const inferNeedsLocalImport = Boolean(
    selectedInferModel?.gated && !selectedInferModel.local_imported
  )
  const selectedModelForImport = tab === 'serve' ? serveModel : inferModel
  const customRangeValid = Boolean(
    selectedServeModel &&
    layerStart >= 0 &&
    layerEnd > layerStart &&
    layerEnd <= selectedServeModel.num_layers
  )
  const customAddsMissingCoverage = Boolean(
    servingPlan?.segments.some(
      (segment) =>
        segment.status === 'missing' && layerStart < segment.end && layerEnd > segment.start
    )
  )
  const customMayNeedConfirmation = Boolean(
    servingMode === 'custom' &&
    customRangeValid &&
    servingPlan?.missing_ranges.length &&
    !customAddsMissingCoverage
  )
  const servingPlanAuthoritative = isServingPlanAuthoritative(servingPlan)
  const servingPlanState = servingPlanStatus(servingPlan, servingPlanError, servingPlanLoading)
  const servePresentation = selectedServeModel
    ? modelPresentation(selectedServeModel, servingPlan)
    : null
  const inferencePresentation = selectedInferModel
    ? modelPresentation(selectedInferModel, inferencePlan)
    : null
  const nodeStartDisabledReason = !serveModel
    ? 'Choose a model.'
    : !customRangeValid
      ? `Choose a valid range inside zero to ${selectedServeModel?.num_layers ?? 0}.`
      : servingMode === 'recommended' && !servingPlanAuthoritative
        ? 'Wait for a fresh authoritative coverage snapshot, or switch to Custom.'
        : servingMode === 'recommended' && !servingPlan?.recommendation
          ? 'No non-overlapping placement is available; stop a provider or wait for a lease to expire.'
          : null
  const generatorStartDisabledReason = !inferModel
    ? 'Choose a model.'
    : inferNeedsLocalImport
      ? 'Download or import the approved local model files before starting inference.'
      : null

  const refreshServingPlan = useCallback(async (): Promise<ServingPlan | null> => {
    if (!serveModel || !selectedServeModel) return null
    if (servingPlanPromiseRef.current) await servingPlanPromiseRef.current
    const request = (async (): Promise<ServingPlan | null> => {
      const boundedCount = Math.min(Math.max(1, layerCount), selectedServeModel.num_layers)
      setServingPlanLoading(true)
      try {
        const plan = await api.getServingPlan(serveModel, boundedCount)
        setServingPlan(plan)
        setServingPlanError(null)
        setServingPlanReceivedAt(new Date())
        const planState = `${plan.snapshot_source}:${plan.snapshot_stale}:${plan.refreshing}:${plan.coverage_revision}`
        if (lastServingPlanStateRef.current !== planState) {
          recordDiagnostic({
            source: 'network',
            severity: isServingPlanAuthoritative(plan) ? 'success' : 'warning',
            summary: isServingPlanAuthoritative(plan)
              ? `Fresh serving plan received for ${serveModel}.`
              : `Serving plan for ${serveModel} is provisional while remote discovery refreshes.`,
            details: {
              model_id: serveModel,
              layer_count: boundedCount,
              snapshot_source: plan.snapshot_source,
              snapshot_stale: plan.snapshot_stale,
              refreshing: plan.refreshing,
              coverage_revision: plan.coverage_revision,
              recommendation: plan.recommendation,
              selected_route: plan.selected_route
            }
          })
          lastServingPlanStateRef.current = planState
        }
        if (
          servingMode === 'recommended' &&
          isServingPlanAuthoritative(plan) &&
          plan.recommendation
        ) {
          setLayerStart(plan.recommendation.layer_start)
          setLayerEnd(plan.recommendation.layer_end)
        }
        return plan
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Could not refresh layer coverage'
        setServingPlanError(message)
        log(message, 'error')
        return null
      } finally {
        setServingPlanLoading(false)
      }
    })()
    servingPlanPromiseRef.current = request
    try {
      return await request
    } finally {
      if (servingPlanPromiseRef.current === request) servingPlanPromiseRef.current = null
    }
  }, [layerCount, log, selectedServeModel, serveModel, servingMode])

  const refreshInferencePlan = useCallback(async (): Promise<void> => {
    if (!inferModel || !selectedInferModel) return
    if (inferencePlanPromiseRef.current) await inferencePlanPromiseRef.current
    const request = (async (): Promise<void> => {
      const capacity = Math.max(1, Math.ceil(selectedInferModel.num_layers / 2))
      try {
        setInferencePlan(await api.getServingPlan(inferModel, capacity))
      } catch {
        setInferencePlan(null)
      }
    })()
    inferencePlanPromiseRef.current = request
    try {
      await request
    } finally {
      if (inferencePlanPromiseRef.current === request) inferencePlanPromiseRef.current = null
    }
  }, [inferModel, selectedInferModel])

  useEffect(() => {
    if (!serveModel || !selectedServeModel) return
    void refreshServingPlan()
    const interval = window.setInterval(() => void refreshServingPlan(), 5_000)
    return () => window.clearInterval(interval)
  }, [refreshServingPlan, selectedServeModel, serveModel])

  useEffect(() => {
    if (!inferModel || !selectedInferModel) return
    void refreshInferencePlan()
    const interval = window.setInterval(() => void refreshInferencePlan(), 5_000)
    return () => window.clearInterval(interval)
  }, [inferModel, refreshInferencePlan, selectedInferModel])

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
        window.setTimeout(
          () => {
            void pollHuggingFaceLogin(flowId, nextInterval)
          },
          Math.max(2, nextInterval) * 1000
        )
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
      window.setTimeout(
        () => {
          void pollHuggingFaceLogin(flow.flow_id, flow.interval)
        },
        Math.max(2, flow.interval) * 1000
      )
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
          ? `Deleted all ${modelId} cached files (${result.deleted_size})`
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

  const updateLayerCount = useCallback(
    (next: number) => {
      const total = selectedServeModel?.num_layers ?? 1
      setLayerCount(Math.min(Math.max(1, next), total))
    },
    [selectedServeModel]
  )

  const applyRecommendation = useCallback((plan: ServingPlan) => {
    if (!plan.recommendation) return
    setLayerCount(plan.requested_layer_count)
    setLayerStart(plan.recommendation.layer_start)
    setLayerEnd(plan.recommendation.layer_end)
    setServingPlan(plan)
  }, [])

  const handleStartNode = useCallback(async () => {
    if (!serveModel || nodeLoading) return
    if (serveNeedsLocalImport) {
      log(`Import a local approved model directory for ${serveModel} before starting.`, 'error')
      return
    }

    const totalLayers = selectedServeModel?.num_layers ?? 0
    if (layerStart < 0 || layerEnd <= layerStart || layerEnd > totalLayers) {
      log(`Choose a layer range inside 0-${totalLayers}.`, 'error')
      return
    }

    setNodeLoading(true)

    try {
      const accessOk = await ensureLocalImport(serveModel)
      if (!accessOk) return

      const requestedCount = servingMode === 'recommended' ? layerCount : layerEnd - layerStart
      const latestPlan = await api.getServingPlan(serveModel, requestedCount)
      setServingPlan(latestPlan)
      setServingPlanReceivedAt(new Date())
      setServingPlanError(null)

      let requestedStart = layerStart
      let requestedEnd = layerEnd
      if (servingMode === 'recommended') {
        if (!isServingPlanAuthoritative(latestPlan)) {
          log(
            'Remote coverage discovery is still refreshing. Wait for a FRESH snapshot before using Recommended.',
            'error'
          )
          return
        }
        if (!latestPlan.recommendation) {
          log(
            'The placement coordinator has no non-overlapping capacity for this request.',
            'error'
          )
          return
        }
        requestedStart = latestPlan.recommendation.layer_start
        requestedEnd = latestPlan.recommendation.layer_end
        applyRecommendation(latestPlan)
      }

      log(`Starting node: ${serveModel} layers ${requestedStart}-${requestedEnd}...`)

      const startParams = {
        model_name: serveModel,
        layer_start: requestedStart,
        layer_end: requestedEnd,
        dht_prefix: 'distribllm',
        initial_peers: [],
        device,
        coverage_revision: latestPlan.coverage_revision,
        placement_mode: servingMode,
        layer_capacity: requestedCount,
        placement_revision: latestPlan.placement?.topology_revision,
        placement_idempotency_key: window.crypto.randomUUID()
      }

      let previousStage = ''
      const observe = (job: LifecycleJob): void => {
        setNodeProgress(job.stage)
        if (job.stage !== previousStage) {
          previousStage = job.stage
          log(job.detail, job.status === 'failed' ? 'error' : 'info')
        }
      }
      const submitted = await api.startNodeAsync(startParams)
      setNodeJobId(submitted.job_id)
      let completed = await waitForLifecycleJob(submitted, observe)
      let res = startResultFromJob(completed)

      if (res.status === 'error' && res.error === 'local_replica_confirmation_required') {
        if (!window.confirm(res.message ?? 'Start an additional identical local replica?')) {
          log('Additional local replica was not started.', 'info')
          return
        }
        const confirmedJob = await api.startNodeAsync({
          ...startParams,
          confirm_local_replica: true
        })
        setNodeJobId(confirmedJob.job_id)
        completed = await waitForLifecycleJob(confirmedJob, observe)
        res = startResultFromJob(completed)
      }

      if (
        res.status === 'error' &&
        res.plan &&
        (res.error === 'coverage_revision_stale' ||
          res.error === 'redundancy_confirmation_required')
      ) {
        setServingPlan(res.plan)
        if (servingMode === 'recommended') applyRecommendation(res.plan)
        if (res.error === 'coverage_revision_stale') {
          log(res.message ?? 'Layer coverage changed. Review the fresh plan.', 'error')
          return
        }
        if (!window.confirm(res.message ?? 'Confirm redundant serving range.')) {
          log('Redundant serving range was not started.', 'info')
          return
        }
        const confirmedJob = await api.startNodeAsync({
          ...startParams,
          coverage_revision: res.plan.coverage_revision,
          confirm_redundancy: true
        })
        setNodeJobId(confirmedJob.job_id)
        completed = await waitForLifecycleJob(confirmedJob, observe)
        res = startResultFromJob(completed)
      }

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
        await Promise.all([refreshModelsAndImports(), refreshServingPlan(), refreshInferencePlan()])
      } else if (res.status === 'already_running') {
        log('Node already running', 'info')
        setNodeRunning(true)
      }
    } catch (err) {
      log(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    } finally {
      setNodeLoading(false)
      setNodeProgress('starting')
      setNodeJobId(null)
    }
  }, [
    serveModel,
    nodeLoading,
    serveNeedsLocalImport,
    selectedServeModel,
    log,
    layerStart,
    layerEnd,
    layerCount,
    servingMode,
    device,
    ensureLocalImport,
    applyRecommendation,
    refreshModelsAndImports,
    refreshServingPlan,
    refreshInferencePlan
  ])

  const handleCancelNodeStart = useCallback(async () => {
    if (!nodeJobId) return
    try {
      await api.cancelLifecycleJob(nodeJobId)
      setNodeProgress('cancelling')
      log('Node startup cancellation requested.', 'info')
    } catch (err) {
      log(err instanceof Error ? err.message : 'Could not cancel node startup', 'error')
    }
  }, [log, nodeJobId])

  const handleServeMissingRange = useCallback(async () => {
    if (!selectedInferModel || !inferencePlan) return
    const firstGap = inferencePlan.missing_ranges[0]
    const capacity = firstGap
      ? Math.max(1, firstGap.end - firstGap.start)
      : Math.max(1, Math.ceil(selectedInferModel.num_layers / 2))
    try {
      const plan = await api.getServingPlan(selectedInferModel.id, capacity)
      if (!isServingPlanAuthoritative(plan)) {
        log('Remote coverage is still refreshing. No recommended range was selected.', 'error')
        return
      }
      if (!plan.recommendation) {
        log('The placement coordinator reports no unowned serving capacity.', 'error')
        return
      }
      setServeModel(selectedInferModel.id)
      setServingMode('recommended')
      applyRecommendation(plan)
      setTab('serve')
      log(
        `Selected useful range ${plan.recommendation.layer_start}-${plan.recommendation.layer_end}.`,
        'info'
      )
    } catch (err) {
      log(err instanceof Error ? err.message : 'Could not prepare a serving range', 'error')
    }
  }, [applyRecommendation, inferencePlan, log, selectedInferModel])

  // ---------------------------------------------------------------------------
  // Start generator
  // ---------------------------------------------------------------------------

  const handleStartGenerator = useCallback(async () => {
    if (!inferModel || genLoading) return
    if (!inferencePlan?.current_runnable) {
      log(
        'Connecting the generator DHT client to refresh remote coverage and validate the route.',
        'info'
      )
    }
    if (inferNeedsLocalImport) {
      log(`Import a local approved model directory for ${inferModel} before starting.`, 'error')
      return
    }

    log(`Starting generator: ${inferModel}...`)
    setGenLoading(true)

    try {
      const accessOk = await ensureLocalImport(inferModel)
      if (!accessOk) return

      let previousStage = ''
      const submitted = await api.startGeneratorAsync({
        model_name: inferModel,
        dht_prefix: 'distribllm',
        initial_peers: []
      })
      setGenJobId(submitted.job_id)
      const completed = await waitForLifecycleJob(submitted, (job) => {
        setGenProgress(job.stage)
        if (job.stage !== previousStage) {
          previousStage = job.stage
          log(job.detail, job.status === 'failed' ? 'error' : 'info')
        }
      })
      const res = startResultFromJob(completed)

      if (res.status === 'error') {
        if (res.error === 'local_model_import_required') {
          log(res.message ?? 'Local model import required', 'error')
        } else {
          log(`Error: ${res.message ?? res.error}`, 'error')
        }
      } else {
        const status = await api.getGeneratorStatus()
        setGenReady(status.ready)
        setGeneratorStatus(status)
        if (status.ready) {
          log('Generator route and tensor canary are ready — go to Inference page', 'success')
        } else {
          log(status.reasons.join('; ') || 'Generator route is not ready.', 'error')
        }
      }
    } catch (err) {
      log(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    } finally {
      setGenLoading(false)
      setGenProgress('starting')
      setGenJobId(null)
    }
  }, [inferModel, genLoading, inferNeedsLocalImport, inferencePlan, log, ensureLocalImport])

  const handleCancelGeneratorStart = useCallback(async () => {
    if (!genJobId) return
    try {
      await api.cancelLifecycleJob(genJobId)
      setGenProgress('cancelling')
      log('Generator startup cancellation requested.', 'info')
    } catch (err) {
      log(err instanceof Error ? err.message : 'Could not cancel generator startup', 'error')
    }
  }, [genJobId, log])

  const handleStopGenerator = useCallback(async () => {
    if (!generatorStatus?.components_loaded || genLoading) return

    log('Stopping generator...')
    setGenLoading(true)

    try {
      const res = await api.unloadGenerator()
      if (res.status === 'unloaded') {
        log('Generator stopped and local resources released', 'success')
      } else {
        log(`Generator status: ${res.status}`, 'info')
      }
      setGenReady(false)
      setGeneratorStatus(null)
    } catch (err) {
      log(`Failed: ${err instanceof Error ? err.message : 'Unknown error'}`, 'error')
    } finally {
      setGenLoading(false)
    }
  }, [genLoading, generatorStatus?.components_loaded, log])

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
            Accept this model&apos;s Hugging Face terms first. Then connect your account so
            DistribLLM can download the approved files locally.
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
          {activeDownloadForModel
            ? activeDownloadForModel.status.toUpperCase()
            : 'DOWNLOAD APPROVED MODEL'}
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
    <div className="flex h-full flex-col overflow-hidden xl:flex-row">
      {/* Left panel */}
      <div className="flex min-h-0 w-full min-w-0 flex-col border-b border-border xl:w-[480px] xl:min-w-[480px] xl:border-r xl:border-b-0">
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
              type="button"
              role="tab"
              aria-selected={tab === id}
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
                Provider role: load a bounded transformer-layer range on this device and advertise
                it to the swarm. This does not start a chat client.
              </p>
              {/* Model dropdown */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Model</label>
                {modelsLoading ? (
                  <div className="h-10 animate-pulse rounded-lg bg-bg-surface" />
                ) : (
                  <select
                    aria-label="Model to serve"
                    value={serveModel}
                    onChange={(e) => handleServeModelChange(e.target.value)}
                    disabled={nodeLoading}
                    className={inputCls}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id} — {modelPresentation(m, null).primary} — {tuningLabel(m)}
                      </option>
                    ))}
                  </select>
                )}

                {/* Model info */}
                {selectedServeModel && (
                  <div className="flex flex-col gap-1">
                    <p className="font-mono text-[10px] text-text-dim">
                      {selectedServeModel.num_layers} layers total · {selectedServeModel.vram_gb}GB
                      VRAM · {selectedServeModel.gated ? 'gated' : 'open'} ·{' '}
                      {tuningLabel(selectedServeModel)}
                    </p>
                    {servePresentation && (
                      <p className="font-mono text-[10px] text-cyan">
                        {servePresentation.primary} · {servePresentation.secondary}
                      </p>
                    )}
                  </div>
                )}
              </div>

              {renderLocalImportPanel(selectedServeModel)}

              <div className="grid h-10 grid-cols-2 rounded-lg border border-border bg-bg-surface p-1">
                {(['recommended', 'custom'] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    aria-pressed={servingMode === mode}
                    onClick={() => {
                      setServingMode(mode)
                      if (mode === 'recommended' && servingPlanAuthoritative) {
                        applyRecommendation(servingPlan)
                      }
                    }}
                    disabled={nodeLoading}
                    className={`rounded-md font-mono text-[10px] font-semibold uppercase transition-colors ${
                      servingMode === mode
                        ? 'bg-bg-elevated text-cyan shadow-sm'
                        : 'text-text-dim hover:text-text-secondary'
                    }`}
                  >
                    {mode}
                  </button>
                ))}
              </div>

              {servingMode === 'recommended' ? (
                <div className="flex flex-col gap-1.5">
                  <label className={labelCls}>Layer Capacity</label>
                  <div className="grid h-10 grid-cols-[40px_1fr_40px] overflow-hidden rounded-lg border border-border-bright bg-bg-surface">
                    <button
                      type="button"
                      onClick={() => updateLayerCount(layerCount - 1)}
                      disabled={nodeLoading || layerCount <= 1}
                      title="Serve one fewer layer"
                      className="border-r border-border font-mono text-lg text-text-secondary hover:bg-bg-hover disabled:opacity-30"
                    >
                      −
                    </button>
                    <input
                      aria-label="Layer capacity"
                      type="number"
                      value={layerCount}
                      min={1}
                      max={selectedServeModel?.num_layers ?? 1}
                      onChange={(e) => updateLayerCount(Number(e.target.value))}
                      disabled={nodeLoading}
                      className="w-full bg-transparent text-center font-mono text-[13px] text-text-primary outline-none"
                    />
                    <button
                      type="button"
                      onClick={() => updateLayerCount(layerCount + 1)}
                      disabled={nodeLoading || layerCount >= (selectedServeModel?.num_layers ?? 1)}
                      title="Serve one more layer"
                      className="border-l border-border font-mono text-lg text-text-secondary hover:bg-bg-hover disabled:opacity-30"
                    >
                      +
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex gap-3">
                  <div className="flex flex-1 flex-col gap-1.5">
                    <label className={labelCls}>Layer Start</label>
                    <input
                      aria-label="First layer to serve"
                      type="number"
                      value={layerStart}
                      min={0}
                      max={Math.max(0, layerEnd - 1)}
                      onChange={(e) => setLayerStart(Number(e.target.value))}
                      disabled={nodeLoading}
                      className={inputCls}
                    />
                  </div>
                  <div className="flex flex-1 flex-col gap-1.5">
                    <label className={labelCls}>Layer End</label>
                    <input
                      aria-label="Layer end boundary"
                      type="number"
                      value={layerEnd}
                      min={layerStart + 1}
                      max={selectedServeModel?.num_layers ?? 1}
                      onChange={(e) => setLayerEnd(Number(e.target.value))}
                      disabled={nodeLoading}
                      className={inputCls}
                    />
                  </div>
                </div>
              )}

              {servingPlan && (
                <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg-surface px-3 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className={labelCls}>Coverage Snapshot</span>
                    <span
                      className={`rounded border px-1.5 py-0.5 font-mono text-[9px] ${
                        servingPlanState === 'fresh'
                          ? 'border-green/20 bg-green/5 text-green'
                          : servingPlanState === 'unavailable'
                            ? 'border-red/20 bg-red/5 text-red'
                            : 'border-amber/20 bg-amber/5 text-amber'
                      }`}
                    >
                      {servingPlanState === 'fresh'
                        ? 'FRESH'
                        : servingPlanState === 'unavailable'
                          ? 'UNAVAILABLE'
                          : 'DISCOVERING'}
                    </span>
                  </div>
                  <p className="font-mono text-[9px] text-text-dim">
                    REV {servingPlan.coverage_revision.slice(0, 7)} ·{' '}
                    {servingPlan.snapshot_source === 'placement_coordinator'
                      ? 'PLACEMENT AUTHORITY'
                      : servingPlan.snapshot_source === 'local_only'
                        ? 'LOCAL-ONLY'
                        : servingPlan.snapshot_source === 'validated_dht'
                          ? 'VALIDATED DHT'
                          : 'DHT CACHE'}
                    {servingPlanReceivedAt
                      ? ` · RECEIVED ${servingPlanReceivedAt.toLocaleTimeString()}`
                      : ''}
                  </p>
                  <CoverageStrip plan={servingPlan} showRecommendation={servingPlanAuthoritative} />
                  <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[9px] text-text-dim">
                    <span>
                      <i className="mr-1 inline-block h-2 w-2 bg-red/40" />
                      Missing
                    </span>
                    <span>
                      <i className="mr-1 inline-block h-2 w-2 bg-green/40" />
                      Covered
                    </span>
                    <span>
                      <i className="mr-1 inline-block h-2 w-2 bg-amber/50" />
                      Redundant
                    </span>
                    {servingPlanAuthoritative && servingPlan.recommendation && (
                      <span>
                        <i className="mr-1 inline-block h-2 w-2 border border-cyan" />
                        Recommended
                      </span>
                    )}
                  </div>
                  {!servingPlanAuthoritative ? (
                    <p className="font-mono text-[10px] leading-relaxed text-amber">
                      Remote discovery has not produced a fresh snapshot yet. The cyan
                      recommendation is hidden and Recommended cannot start a node. Wait for FRESH,
                      or use Custom; the backend will still validate the range before loading
                      layers.
                    </p>
                  ) : servingPlan.recommendation ? (
                    <p className="font-mono text-[10px] leading-relaxed text-text-secondary">
                      {servingPlan.recommendation.completes_route && !servingPlan.current_runnable
                        ? `Fills ${servingPlan.recommendation.layer_start}-${servingPlan.recommendation.layer_end}; model becomes runnable.`
                        : servingPlan.recommendation.adds_missing_coverage
                          ? `Adds missing coverage at ${servingPlan.recommendation.layer_start}-${servingPlan.recommendation.layer_end}; still needs ${formatRanges(servingPlan.projected_missing_ranges)}.`
                          : `Adds redundancy at ${servingPlan.recommendation.layer_start}-${servingPlan.recommendation.layer_end}.`}
                    </p>
                  ) : (
                    <p className="font-mono text-[10px] leading-relaxed text-amber">
                      All compatible ranges are reserved. Stop an existing provider or wait for an
                      abandoned lease to expire before starting another node.
                    </p>
                  )}
                  {servingPlanError && (
                    <p className="break-words font-mono text-[10px] text-red">
                      Last refresh failed: {servingPlanError}
                    </p>
                  )}
                  {servingPlan.selected_route.length > 0 && (
                    <div className="flex flex-col gap-1 border-t border-border pt-2">
                      <span className={labelCls}>Discovered active route</span>
                      {servingPlan.selected_route.map((provider) => (
                        <p
                          key={`${provider.peer_id}-${provider.layer_start}-${provider.layer_end}`}
                          className="font-mono text-[9px] text-text-secondary"
                        >
                          {provider.layer_start}-{provider.layer_end} ·{' '}
                          {provider.peer_id.slice(0, 12)}…
                        </p>
                      ))}
                    </div>
                  )}
                  {servingMode === 'custom' && !customRangeValid && (
                    <p className="font-mono text-[10px] text-red">
                      Range must stay inside 0-{selectedServeModel?.num_layers ?? 0} with end
                      greater than start.
                    </p>
                  )}
                  {customMayNeedConfirmation && (
                    <p className="font-mono text-[10px] text-amber">
                      This range adds no missing layers while the route still needs{' '}
                      {formatRanges(servingPlan.missing_ranges)}. Starting it requires confirmation.
                    </p>
                  )}
                </div>
              )}

              {/* Device */}
              <div className="flex flex-col gap-1.5">
                <label className={labelCls}>Device</label>
                <select
                  aria-label="Serving compute device"
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
                type="button"
                aria-label="Start serving selected model layers"
                onClick={() => void (nodeLoading ? handleCancelNodeStart() : handleStartNode())}
                disabled={
                  !nodeLoading &&
                  (!serveModel ||
                    !customRangeValid ||
                    (servingMode === 'recommended' &&
                      (!servingPlanAuthoritative || !servingPlan?.recommendation)))
                }
                title={
                  servingMode === 'recommended' &&
                  (!servingPlanAuthoritative || !servingPlan?.recommendation)
                    ? 'Wait for an authoritative placement with available capacity.'
                    : 'Start serving the selected layers.'
                }
                className={`
                  w-full rounded-xl border py-3 font-mono text-[12px] font-semibold
                  transition-all duration-150
                  ${
                    nodeLoading
                      ? 'cursor-pointer border-red/30 bg-red/10 text-red hover:bg-red/20'
                      : !serveModel ||
                          !customRangeValid ||
                          (servingMode === 'recommended' &&
                            (!servingPlanAuthoritative || !servingPlan?.recommendation))
                        ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                        : 'cursor-pointer border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
                  }
                `}
              >
                {nodeLoading ? `CANCEL ${nodeProgress.toUpperCase()}` : 'START NODE'}
              </button>
              {!nodeLoading && nodeStartDisabledReason && (
                <p className="font-mono text-[10px] leading-relaxed text-amber">
                  Start unavailable: {nodeStartDisabledReason}
                </p>
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
                    aria-label="Model for remote inference"
                    value={inferModel}
                    onChange={(e) => {
                      setInferModel(e.target.value)
                      setInferencePlan(null)
                    }}
                    disabled={genLoading || Boolean(generatorStatus?.components_loaded)}
                    className={inputCls}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id} — {modelPresentation(m, null).primary} — {tuningLabel(m)}
                      </option>
                    ))}
                  </select>
                )}

                {inferencePresentation && (
                  <div
                    className={`rounded-lg border px-3 py-2 font-mono text-[10px] ${
                      inferencePresentation.tone === 'ready'
                        ? 'border-green/20 bg-green/5 text-green'
                        : inferencePresentation.tone === 'failed'
                          ? 'border-red/20 bg-red/5 text-red'
                          : 'border-amber/30 bg-amber/10 text-amber'
                    }`}
                  >
                    <p className="font-semibold">{inferencePresentation.primary}</p>
                    <p className="mt-1 leading-relaxed">{inferencePresentation.secondary}</p>
                    <p className="mt-1 text-text-secondary">Next: {inferencePresentation.action}</p>
                  </div>
                )}

                {selectedInferModel && inferencePlan && (
                  <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg-surface px-3 py-3">
                    <p
                      className={`font-mono text-[10px] ${
                        inferencePlan.current_runnable ? 'text-green' : 'text-amber'
                      }`}
                    >
                      {!isServingPlanAuthoritative(inferencePlan)
                        ? 'Discovering remote providers; this coverage is provisional'
                        : inferencePlan.current_runnable
                          ? inferencePlan.route_kind === 'single_provider'
                            ? 'Complete through one full-model provider'
                            : `Complete through ${inferencePlan.selected_route.length} providers`
                          : `Needs layers ${formatRanges(inferencePlan.missing_ranges)}`}
                    </p>
                    <CoverageStrip plan={inferencePlan} showRecommendation={false} />
                    {inferencePlan.selected_route.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {inferencePlan.selected_route.map((routeNode) => (
                          <span
                            key={`${routeNode.peer_id}-${routeNode.layer_start}-${routeNode.layer_end}`}
                            title={routeNode.peer_id}
                            className="rounded border border-green/20 bg-green/5 px-2 py-1 font-mono text-[9px] text-green"
                          >
                            {routeNode.layer_start}-{routeNode.layer_end} ·{' '}
                            {routeNode.peer_id.slice(0, 8)}
                          </span>
                        ))}
                      </div>
                    )}
                    {inferencePlan.standby_ranges.length > 0 && (
                      <p className="font-mono text-[9px] leading-relaxed text-text-dim">
                        Standby:{' '}
                        {inferencePlan.standby_ranges
                          .map((routeNode) => `${routeNode.layer_start}-${routeNode.layer_end}`)
                          .join(', ')}
                      </p>
                    )}
                    {!inferencePlan.current_runnable && (
                      <div className="flex flex-col gap-2">
                        <p className="font-mono text-[9px] leading-relaxed text-text-dim">
                          This preflight snapshot may be local-only. Start Generator connects its
                          own DHT client and performs the authoritative route and tensor checks.
                        </p>
                        <button
                          type="button"
                          onClick={() => void handleServeMissingRange()}
                          className="h-9 rounded-lg border border-amber/30 bg-amber/10 font-mono text-[10px] font-semibold text-amber hover:bg-amber/20"
                        >
                          SERVE MISSING RANGE
                        </button>
                      </div>
                    )}
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
              ) : generatorStatus?.components_loaded ? (
                <div className="flex flex-col gap-3 rounded-lg border border-amber/30 bg-amber/10 px-4 py-3">
                  <div>
                    <p className="font-mono text-[12px] text-amber">
                      Generator {generatorStatus.state}
                    </p>
                    <p className="mt-1 text-[11px] leading-relaxed text-text-secondary">
                      {generatorStatus.reasons.join('; ') || 'The current route is not usable.'}
                    </p>
                    {generatorStatus.health && (
                      <p className="mt-2 font-mono text-[10px] leading-relaxed text-text-dim">
                        Health {generatorStatus.health.health_revision}
                        {' · '}active probes {generatorStatus.health.active_probes ?? 0}
                        {generatorStatus.health.last_discovery_error
                          ? ` · discovery ${generatorStatus.health.last_discovery_error}`
                          : ''}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => void handleStopGenerator()}
                    disabled={genLoading}
                    className="w-full rounded-lg border border-red/30 bg-red/10 py-2.5 font-mono text-[11px] font-semibold text-red hover:bg-red/20 disabled:opacity-50"
                  >
                    {genLoading ? 'UNLOADING...' : 'UNLOAD GENERATOR'}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  aria-label="Start inference client"
                  onClick={() =>
                    void (genLoading ? handleCancelGeneratorStart() : handleStartGenerator())
                  }
                  disabled={!genLoading && Boolean(generatorStartDisabledReason)}
                  className={`
                    w-full rounded-xl border py-3 font-mono text-[12px] font-semibold
                    transition-all duration-150
                    ${
                      genLoading
                        ? 'cursor-pointer border-red/30 bg-red/10 text-red hover:bg-red/20'
                        : generatorStartDisabledReason
                          ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                          : 'cursor-pointer border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
                    }
                  `}
                >
                  {genLoading ? `CANCEL ${genProgress.toUpperCase()}` : 'START GENERATOR'}
                </button>
              )}
              {!genLoading &&
                !genReady &&
                !generatorStatus?.components_loaded &&
                generatorStartDisabledReason && (
                  <p className="font-mono text-[10px] leading-relaxed text-amber">
                    Start unavailable: {generatorStartDisabledReason}
                  </p>
                )}
            </>
          )}
        </div>
      </div>

      {/* Activity log */}
      <div className="flex min-h-[220px] flex-1 flex-col">
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
