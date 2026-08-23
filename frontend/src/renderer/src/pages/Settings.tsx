/**
 * Settings.tsx
 * Application settings page.
 *
 * Currently manages:
 *   - Optional HuggingFace token diagnostics/fallback settings.
 */

import React, { useState, useEffect, useCallback } from 'react'
import { api, type RuntimeSnapshot } from '../api/client'
import {
  clearDiagnosticEvents,
  getDiagnosticEvents,
  recordDiagnostic,
  subscribeDiagnostics,
  type DiagnosticEvent,
  type DiagnosticSeverity
} from '../api/diagnostics'

type SaveState = 'idle' | 'saving' | 'saved' | 'error' | 'deleting'
type LauncherConfig = Awaited<ReturnType<Window['api']['getBackendLauncherConfig']>>
type LauncherStatus = Awaited<ReturnType<Window['api']['getBackendLauncherStatus']>>

const launcherStatusStyle: Record<LauncherStatus['state'], string> = {
  idle: 'border-border text-text-dim',
  needs_setup: 'border-amber/30 bg-amber/10 text-amber',
  checking: 'border-cyan/30 bg-cyan-dim text-cyan',
  missing_wsl: 'border-red/20 bg-red/5 text-red',
  missing_distro: 'border-red/20 bg-red/5 text-red',
  installing_backend: 'border-cyan/30 bg-cyan-dim text-cyan',
  starting_backend: 'border-cyan/30 bg-cyan-dim text-cyan',
  ready: 'border-green/20 bg-green/5 text-green',
  stopping: 'border-amber/30 bg-amber/10 text-amber',
  failed: 'border-red/20 bg-red/5 text-red'
}

const diagnosticStatusStyle: Record<DiagnosticSeverity, string> = {
  info: 'border-cyan/20 bg-cyan/5 text-cyan',
  success: 'border-green/20 bg-green/5 text-green',
  warning: 'border-amber/20 bg-amber/5 text-amber',
  error: 'border-red/20 bg-red/5 text-red'
}

export default function Settings(): React.JSX.Element {
  const [tokenInput, setTokenInput] = useState('')
  const [tokenPreview, setTokenPreview] = useState<string | null>(null)
  const [tokenSet, setTokenSet] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [launcherConfig, setLauncherConfig] = useState<LauncherConfig | null>(null)
  const [launcherStatus, setLauncherStatus] = useState<LauncherStatus | null>(null)
  const [launcherAction, setLauncherAction] = useState<
    'idle' | 'saving' | 'starting' | 'stopping' | 'restarting' | 'exporting'
  >('idle')
  const [launcherError, setLauncherError] = useState<string | null>(null)
  const [launcherReportStatus, setLauncherReportStatus] = useState<{
    message: string
    ok: boolean
  } | null>(null)
  const [diagnosticEvents, setDiagnosticEvents] = useState<DiagnosticEvent[]>(getDiagnosticEvents)
  const [runtimeSnapshot, setRuntimeSnapshot] = useState<RuntimeSnapshot | null>(null)
  const [diagnosticsRefreshing, setDiagnosticsRefreshing] = useState(false)
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // Load current settings on mount
  // ---------------------------------------------------------------------------

  const loadSettings = useCallback(async () => {
    try {
      const res = await api.getSettings()
      setTokenSet(res.token_set)
      setTokenPreview(res.token_preview ?? null)
    } catch {
      // Settings endpoint unreachable — backend may not be running
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSettings()
  }, [loadSettings])

  const refreshDiagnostics = useCallback(async () => {
    setDiagnosticsRefreshing(true)
    try {
      setRuntimeSnapshot(await api.getRuntimeSnapshot())
      setDiagnosticsError(null)
    } catch (error) {
      setDiagnosticsError(
        error instanceof Error ? error.message : 'Could not read backend runtime diagnostics'
      )
    } finally {
      setDiagnosticsRefreshing(false)
    }
  }, [])

  useEffect(() => {
    const unsubscribe = subscribeDiagnostics(setDiagnosticEvents)
    void refreshDiagnostics()
    return unsubscribe
  }, [refreshDiagnostics])

  useEffect(() => {
    void Promise.all([
      window.api.getBackendLauncherConfig(),
      window.api.getBackendLauncherStatus()
    ]).then(([config, status]) => {
      setLauncherConfig(config)
      setLauncherStatus(status)
    })
    return window.api.onBackendLauncherStatus(setLauncherStatus)
  }, [])

  const updateLauncherConfig = useCallback(
    <Key extends keyof LauncherConfig>(key: Key, value: LauncherConfig[Key]) => {
      setLauncherConfig((current) => (current ? { ...current, [key]: value } : current))
    },
    []
  )

  const saveLauncherConfig = useCallback(
    async (showProgress = true): Promise<LauncherConfig | null> => {
      if (!launcherConfig) return null
      if (showProgress) setLauncherAction('saving')
      setLauncherError(null)
      try {
        const saved = await window.api.saveBackendLauncherConfig(launcherConfig)
        setLauncherConfig(saved)
        return saved
      } catch (error) {
        setLauncherError(error instanceof Error ? error.message : 'Could not save backend settings')
        return null
      } finally {
        if (showProgress) setLauncherAction('idle')
      }
    },
    [launcherConfig]
  )

  const runLauncherAction = useCallback(
    async (action: 'start' | 'stop' | 'restart') => {
      setLauncherAction(
        action === 'start' ? 'starting' : action === 'stop' ? 'stopping' : 'restarting'
      )
      setLauncherError(null)
      setLauncherReportStatus(null)
      try {
        if (action !== 'stop') {
          const saved = await saveLauncherConfig(false)
          if (!saved) return
        }
        const status =
          action === 'start'
            ? await window.api.startBackend()
            : action === 'stop'
              ? await window.api.stopBackend()
              : await window.api.restartBackend()
        setLauncherStatus(status)
      } catch (error) {
        setLauncherError(error instanceof Error ? error.message : `Could not ${action} backend`)
      } finally {
        setLauncherAction('idle')
      }
    },
    [saveLauncherConfig]
  )

  const exportAcceptanceReport = useCallback(async () => {
    setLauncherAction('exporting')
    setLauncherError(null)
    setLauncherReportStatus(null)
    try {
      const result = await window.api.exportWindowsAcceptanceReport()
      if (!result.canceled) {
        setLauncherReportStatus({
          message: result.reportOk
            ? `Acceptance report saved: ${result.fileName}.`
            : `Acceptance report saved: ${result.fileName}. Required checks remain incomplete.`,
          ok: Boolean(result.reportOk)
        })
      }
    } catch (error) {
      setLauncherError(
        error instanceof Error ? error.message : 'Could not export Windows acceptance report'
      )
    } finally {
      setLauncherAction('idle')
    }
  }, [])

  const exportDiagnostics = useCallback(() => {
    const capturedAt = new Date().toISOString()
    const payload = {
      schema_version: 1,
      captured_at: capturedAt,
      renderer_events: diagnosticEvents,
      backend_runtime: runtimeSnapshot
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `distribllm-diagnostics-${capturedAt.replaceAll(':', '-')}.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
    recordDiagnostic({
      source: 'network',
      severity: 'success',
      summary: 'Diagnostic bundle exported.',
      details: {
        renderer_event_count: diagnosticEvents.length,
        backend_event_count: runtimeSnapshot?.events.length ?? 0
      }
    })
  }, [diagnosticEvents, runtimeSnapshot])

  const displayedDiagnostics = [
    ...diagnosticEvents.map((event) => ({
      id: event.id,
      capturedAt: event.capturedAt,
      source: event.source,
      severity: event.severity,
      summary: event.summary,
      occurrences: event.occurrences
    })),
    ...(runtimeSnapshot?.events ?? []).map((event) => ({
      id: event.event_id,
      capturedAt: event.captured_at,
      source: `backend · ${event.kind} · ${event.phase}`,
      severity: (event.status === 'error'
        ? 'error'
        : event.status === 'success'
          ? 'success'
          : 'info') as DiagnosticSeverity,
      summary: event.message,
      occurrences: 1
    }))
  ]
    .sort((left, right) => right.capturedAt.localeCompare(left.capturedAt))
    .slice(0, 50)
  const lastRouteFailure = runtimeSnapshot?.generator.health?.last_failover?.reasons.at(-1)

  // ---------------------------------------------------------------------------
  // Save token
  // ---------------------------------------------------------------------------

  const handleSave = useCallback(async () => {
    const token = tokenInput.trim()

    if (!token) {
      setErrorMsg('Token must not be empty')
      return
    }
    if (!token.startsWith('hf_')) {
      setErrorMsg("Invalid token — HuggingFace tokens start with 'hf_'")
      return
    }

    setSaveState('saving')
    setErrorMsg(null)

    try {
      const res = await api.saveToken(token)
      if (res.status === 'saved') {
        setTokenSet(true)
        setTokenPreview(res.token_preview ?? null)
        setTokenInput('')
        setSaveState('saved')
        // Reset to idle after 2s
        setTimeout(() => setSaveState('idle'), 2000)
      } else {
        throw new Error('Unexpected response from server')
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to save token')
      setSaveState('error')
    }
  }, [tokenInput])

  // ---------------------------------------------------------------------------
  // Delete token
  // ---------------------------------------------------------------------------

  const handleDelete = useCallback(async () => {
    setSaveState('deleting')
    setErrorMsg(null)

    try {
      await api.deleteToken()
      setTokenSet(false)
      setTokenPreview(null)
      setTokenInput('')
      setSaveState('idle')
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to delete token')
      setSaveState('error')
    }
  }, [])

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'Enter') {
      e.preventDefault()
      void handleSave()
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-7 py-5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">Settings</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            Application configuration
          </p>
        </div>
      </div>

      <div className="flex max-w-3xl flex-col gap-8 p-7">
        {launcherConfig && launcherStatus?.managed && (
          <section className="flex flex-col gap-5 border-b border-border pb-8">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-sm font-semibold text-text-primary">Managed WSL Backend</h2>
                <p className="mt-1 font-mono text-[10px] text-text-dim">
                  {launcherStatus.updatedAt
                    ? new Date(launcherStatus.updatedAt).toLocaleTimeString()
                    : ''}
                </p>
              </div>
              <span
                className={`rounded border px-2.5 py-1 font-mono text-[9px] font-semibold uppercase ${launcherStatusStyle[launcherStatus.state]}`}
              >
                {launcherStatus.state.replaceAll('_', ' ')}
              </span>
            </div>

            <div className="border-l-2 border-border-bright pl-3">
              <p className="text-[12px] text-text-secondary">{launcherStatus.message}</p>
              {launcherStatus.detail && (
                <p className="mt-1 break-words font-mono text-[10px] text-text-dim">
                  {launcherStatus.detail}
                </p>
              )}
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5 font-mono text-[10px] text-text-dim uppercase">
                WSL Distro
                <input
                  value={launcherConfig.distroName}
                  onChange={(event) => updateLauncherConfig('distroName', event.target.value)}
                  className="h-10 rounded border border-border-bright bg-bg-surface px-3 font-mono text-[12px] text-text-primary outline-none focus:border-cyan/40"
                />
              </label>
              <label className="flex flex-col gap-1.5 font-mono text-[10px] text-text-dim uppercase">
                Backend URL
                <input
                  value={launcherConfig.backendUrl}
                  onChange={(event) => updateLauncherConfig('backendUrl', event.target.value)}
                  className="h-10 rounded border border-border-bright bg-bg-surface px-3 font-mono text-[12px] text-text-primary outline-none focus:border-cyan/40"
                />
              </label>
              <label className="flex flex-col gap-1.5 font-mono text-[10px] text-text-dim uppercase md:col-span-2">
                Backend Path in WSL
                <input
                  value={launcherConfig.backendPath}
                  onChange={(event) => updateLauncherConfig('backendPath', event.target.value)}
                  placeholder="/home/user/distribllm/backend"
                  className="h-10 rounded border border-border-bright bg-bg-surface px-3 font-mono text-[12px] text-text-primary outline-none placeholder:text-text-dim focus:border-cyan/40"
                />
              </label>
              <label className="flex flex-col gap-1.5 font-mono text-[10px] text-text-dim uppercase">
                Network Mode
                <select
                  value={launcherConfig.networkMode}
                  onChange={(event) =>
                    updateLauncherConfig(
                      'networkMode',
                      event.target.value as LauncherConfig['networkMode']
                    )
                  }
                  className="h-10 rounded border border-border-bright bg-bg-surface px-3 font-mono text-[12px] text-text-primary outline-none focus:border-cyan/40"
                >
                  <option value="auto">Auto</option>
                  <option value="relay">Relay</option>
                  <option value="direct">Direct</option>
                </select>
              </label>
              <label className="flex flex-col gap-1.5 font-mono text-[10px] text-text-dim uppercase">
                Relay Wait Seconds
                <input
                  type="number"
                  min={0}
                  max={3600}
                  value={launcherConfig.relayWaitTimeoutSeconds}
                  onChange={(event) =>
                    updateLauncherConfig('relayWaitTimeoutSeconds', Number(event.target.value))
                  }
                  className="h-10 rounded border border-border-bright bg-bg-surface px-3 font-mono text-[12px] text-text-primary outline-none focus:border-cyan/40"
                />
              </label>
              <label className="flex flex-col gap-1.5 font-mono text-[10px] text-text-dim uppercase md:col-span-2">
                Bootstrap Peers
                <textarea
                  rows={3}
                  value={launcherConfig.initialPeers.join('\n')}
                  onChange={(event) =>
                    updateLauncherConfig(
                      'initialPeers',
                      event.target.value
                        .split('\n')
                        .map((value) => value.trim())
                        .filter(Boolean)
                    )
                  }
                  className="resize-y rounded border border-border-bright bg-bg-surface px-3 py-2 font-mono text-[11px] text-text-primary outline-none focus:border-cyan/40"
                />
              </label>
              <label className="flex flex-col gap-1.5 font-mono text-[10px] text-text-dim uppercase md:col-span-2">
                Trusted Relays
                <textarea
                  rows={3}
                  value={launcherConfig.trustedRelays.join('\n')}
                  onChange={(event) =>
                    updateLauncherConfig(
                      'trustedRelays',
                      event.target.value
                        .split('\n')
                        .map((value) => value.trim())
                        .filter(Boolean)
                    )
                  }
                  className="resize-y rounded border border-border-bright bg-bg-surface px-3 py-2 font-mono text-[11px] text-text-primary outline-none focus:border-cyan/40"
                />
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-5">
              <label className="flex items-center gap-2 font-mono text-[10px] text-text-secondary">
                <input
                  type="checkbox"
                  checked={launcherConfig.autoStart}
                  onChange={(event) => updateLauncherConfig('autoStart', event.target.checked)}
                  className="h-4 w-4 accent-cyan"
                />
                Start with app
              </label>
              <label className="flex items-center gap-2 font-mono text-[10px] text-text-secondary">
                <input
                  type="checkbox"
                  checked={launcherConfig.syncDependencies}
                  onChange={(event) =>
                    updateLauncherConfig('syncDependencies', event.target.checked)
                  }
                  className="h-4 w-4 accent-cyan"
                />
                Sync dependencies before launch
              </label>
            </div>

            {launcherError && <p className="font-mono text-[11px] text-red">{launcherError}</p>}
            {launcherReportStatus && (
              <p
                className={`font-mono text-[11px] ${
                  launcherReportStatus.ok ? 'text-green' : 'text-amber'
                }`}
              >
                {launcherReportStatus.message}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void saveLauncherConfig()}
                disabled={launcherAction !== 'idle'}
                className="h-9 rounded border border-border-bright px-4 font-mono text-[10px] font-semibold text-text-secondary disabled:opacity-50"
              >
                SAVE
              </button>
              <button
                type="button"
                onClick={() => void exportAcceptanceReport()}
                disabled={launcherAction !== 'idle'}
                title="Export Windows acceptance report"
                className="h-9 rounded border border-border-bright px-4 font-mono text-[10px] font-semibold text-text-secondary disabled:opacity-50"
              >
                EXPORT REPORT
              </button>
              {launcherStatus.state === 'ready' ? (
                <>
                  <button
                    type="button"
                    onClick={() => void runLauncherAction('restart')}
                    disabled={launcherAction !== 'idle'}
                    className="h-9 rounded border border-cyan/30 bg-cyan-dim px-4 font-mono text-[10px] font-semibold text-cyan disabled:opacity-50"
                  >
                    RESTART
                  </button>
                  <button
                    type="button"
                    onClick={() => void runLauncherAction('stop')}
                    disabled={launcherAction !== 'idle'}
                    className="h-9 rounded border border-red/20 bg-red/5 px-4 font-mono text-[10px] font-semibold text-red disabled:opacity-50"
                  >
                    STOP
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => void runLauncherAction('start')}
                  disabled={launcherAction !== 'idle'}
                  className="h-9 rounded border border-cyan/30 bg-cyan-dim px-4 font-mono text-[10px] font-semibold text-cyan disabled:opacity-50"
                >
                  START
                </button>
              )}
            </div>
          </section>
        )}

        <section className="flex flex-col gap-4 rounded-xl border border-border bg-bg-elevated p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="font-mono text-[13px] font-semibold text-text-primary">
                Runtime Diagnostics
              </h2>
              <p className="mt-1 max-w-xl text-[12px] leading-relaxed text-text-secondary">
                Keeps the latest 200 renderer events across page changes and app restarts. Export
                combines them with the backend runtime snapshot, lifecycle jobs, route state, and
                bounded backend events. Prompts, tokens, passwords, and authorization values are not
                recorded.
              </p>
            </div>
            <span
              className={`rounded border px-2.5 py-1 font-mono text-[9px] font-semibold ${
                diagnosticsError
                  ? diagnosticStatusStyle.error
                  : runtimeSnapshot
                    ? diagnosticStatusStyle.success
                    : diagnosticStatusStyle.warning
              }`}
            >
              {diagnosticsError
                ? 'BACKEND SNAPSHOT FAILED'
                : runtimeSnapshot
                  ? 'SNAPSHOT READY'
                  : 'NO SNAPSHOT'}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ['Renderer events', diagnosticEvents.length],
              ['Backend events', runtimeSnapshot?.events.length ?? 0],
              ['Runtime revision', runtimeSnapshot?.revision ?? '—'],
              ['Local nodes', runtimeSnapshot?.local_nodes.length ?? '—']
            ].map(([label, value]) => (
              <div
                key={label}
                className="rounded-lg border border-border bg-bg-surface px-3 py-2.5"
              >
                <p className="font-mono text-[9px] uppercase text-text-dim">{label}</p>
                <p className="mt-1 font-mono text-[14px] text-text-primary">{value}</p>
              </div>
            ))}
          </div>

          {diagnosticsError && (
            <p className="break-words rounded border border-red/20 bg-red/5 px-3 py-2 font-mono text-[10px] text-red">
              {diagnosticsError}
            </p>
          )}

          {lastRouteFailure && (
            <div className="rounded-lg border border-red/20 bg-red/5 px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <p className="font-mono text-[10px] font-semibold text-red">LAST ROUTE FAILURE</p>
                <p className="font-mono text-[9px] text-text-dim">
                  {lastRouteFailure.failure_class}
                </p>
              </div>
              <p className="mt-2 break-all font-mono text-[10px] text-text-secondary">
                Request {lastRouteFailure.request_id}
              </p>
              <p className="mt-1 font-mono text-[10px] text-text-secondary">
                Layers {lastRouteFailure.layer_start}-{lastRouteFailure.layer_end} · peer{' '}
                {lastRouteFailure.peer_id.slice(0, 16)}…
              </p>
              <p className="mt-2 break-words font-mono text-[9px] leading-relaxed text-text-dim">
                {lastRouteFailure.reason}
              </p>
            </div>
          )}

          <div className="max-h-72 overflow-y-auto rounded-lg border border-border bg-bg-surface">
            {displayedDiagnostics.length === 0 ? (
              <p className="px-4 py-5 font-mono text-[10px] text-text-dim">
                No diagnostic events have been captured yet.
              </p>
            ) : (
              displayedDiagnostics.map((event) => (
                <div
                  key={`${event.source}-${event.id}`}
                  className="flex gap-3 border-b border-border px-3 py-2.5 last:border-b-0"
                >
                  <span
                    className={`mt-0.5 h-fit rounded border px-1.5 py-0.5 font-mono text-[8px] font-semibold uppercase ${diagnosticStatusStyle[event.severity]}`}
                  >
                    {event.severity}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-[11px] text-text-secondary">{event.summary}</p>
                    <p className="mt-1 font-mono text-[9px] text-text-dim">
                      {new Date(event.capturedAt).toLocaleString()} · {event.source}
                      {event.occurrences > 1 ? ` · repeated ${event.occurrences} times` : ''}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void refreshDiagnostics()}
              disabled={diagnosticsRefreshing}
              className="h-9 rounded border border-cyan/30 bg-cyan-dim px-4 font-mono text-[10px] font-semibold text-cyan disabled:opacity-50"
            >
              {diagnosticsRefreshing ? 'REFRESHING...' : 'REFRESH SNAPSHOT'}
            </button>
            <button
              type="button"
              onClick={exportDiagnostics}
              className="h-9 rounded border border-border-bright px-4 font-mono text-[10px] font-semibold text-text-secondary"
            >
              EXPORT DIAGNOSTICS
            </button>
            <button
              type="button"
              onClick={() => clearDiagnosticEvents()}
              disabled={diagnosticEvents.length === 0}
              title="Clear only renderer events; backend events remain bounded by the backend runtime."
              className="h-9 rounded border border-red/20 bg-red/5 px-4 font-mono text-[10px] font-semibold text-red disabled:opacity-40"
            >
              CLEAR RENDERER EVENTS
            </button>
          </div>
        </section>

        {/* HuggingFace Token Section */}
        <section className="flex flex-col gap-4 rounded-xl border border-border bg-bg-elevated p-6">
          {/* Section header */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-mono text-[13px] font-semibold text-text-primary">
                HuggingFace Token Diagnostics
              </h2>
              <p className="mt-1 text-[12px] leading-relaxed text-text-secondary">
                Optional fallback for checking gated model access. The primary gated-model flow is
                to connect Hugging Face from Network so DistribLLM can download approved models
                locally without token paste.
              </p>
            </div>

            {/* Current status badge */}
            {!loading && (
              <span
                className={`flex-shrink-0 flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[9px] font-semibold tracking-wider ${
                  tokenSet
                    ? 'border-green/20 bg-green/5 text-green'
                    : 'border-border bg-bg-surface text-text-dim'
                }`}
              >
                <span
                  className={`inline-block h-1.5 w-1.5 rounded-full ${
                    tokenSet ? 'bg-green' : 'bg-text-dim'
                  }`}
                />
                {tokenSet ? 'TOKEN SET' : 'NOT SET'}
              </span>
            )}
          </div>

          {/* Current token preview */}
          {tokenSet && tokenPreview && (
            <div className="flex items-center gap-3 rounded-lg border border-green/20 bg-green/5 px-4 py-3">
              <span className="font-mono text-[11px] text-text-dim">Current token:</span>
              <span className="font-mono text-[12px] text-green">{tokenPreview}</span>
              <span className="ml-auto font-mono text-[10px] text-text-dim">saved to disk</span>
            </div>
          )}

          {/* Token input */}
          <div className="flex flex-col gap-2">
            <label
              htmlFor="hf-token-diagnostic"
              className="font-mono text-[10px] tracking-widest text-text-dim uppercase"
            >
              {tokenSet ? 'Replace Token' : 'Enter Token'}
            </label>
            <div className="flex gap-2">
              <input
                id="hf-token-diagnostic"
                type="password"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="hf_xxxxxxxxxxxxxxxxxxxxxxxx"
                disabled={saveState === 'saving'}
                className="
                  flex-1 rounded-xl border border-border-bright bg-bg-surface
                  px-4 py-3 font-mono text-[13px] text-text-primary outline-none
                  placeholder:text-text-dim focus:border-cyan/40
                  transition-colors duration-150 disabled:opacity-50
                "
              />
              <button
                type="button"
                aria-label={
                  tokenSet ? 'Replace saved Hugging Face token' : 'Save Hugging Face token'
                }
                onClick={() => void handleSave()}
                disabled={!tokenInput.trim() || saveState === 'saving'}
                className={`
                  flex-shrink-0 rounded-xl border px-5 py-3
                  font-mono text-[12px] font-semibold tracking-wide
                  transition-all duration-150
                  ${
                    saveState === 'saved'
                      ? 'border-green/30 bg-green/10 text-green'
                      : !tokenInput.trim() || saveState === 'saving'
                        ? 'cursor-not-allowed border-border bg-bg-surface text-text-dim opacity-50'
                        : 'cursor-pointer border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
                  }
                `}
              >
                {saveState === 'saving'
                  ? 'SAVING...'
                  : saveState === 'saved'
                    ? '✓ SAVED'
                    : tokenSet
                      ? 'REPLACE'
                      : 'SAVE'}
              </button>
            </div>
          </div>

          {/* Error message */}
          {errorMsg && (
            <div className="rounded-lg border border-red/20 bg-red/5 px-4 py-3">
              <p className="font-mono text-[11px] text-red">{errorMsg}</p>
            </div>
          )}

          {/* Delete button — only shown when token is set */}
          {tokenSet && (
            <div className="flex items-center justify-between border-t border-border pt-4">
              <div>
                <p className="font-mono text-[11px] text-text-secondary">Remove stored token</p>
                <p className="mt-0.5 font-mono text-[10px] text-text-dim">
                  Existing local imports keep working after this token is removed
                </p>
              </div>
              <button
                type="button"
                aria-label="Remove saved Hugging Face token"
                onClick={() => void handleDelete()}
                disabled={saveState === 'deleting'}
                className="
                  rounded-xl border border-red/20 bg-red/5 px-4 py-2
                  font-mono text-[11px] font-semibold text-red
                  transition-all duration-150 hover:bg-red/10
                  disabled:cursor-not-allowed disabled:opacity-50
                "
              >
                {saveState === 'deleting' ? 'REMOVING...' : 'REMOVE TOKEN'}
              </button>
            </div>
          )}
        </section>

        {/* Local gated model flow */}
        <section className="flex flex-col gap-3 rounded-xl border border-border bg-bg-elevated p-6">
          <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
            Gated model local import
          </h2>
          <ol className="flex flex-col gap-2">
            {[
              'Create a free account at huggingface.co',
              'Open the gated model page and accept the model license',
              'Open Network and connect Hugging Face without pasting a token',
              'Download and validate the approved model from Network',
              'Start serving layers or the generator after the import validates'
            ].map((step, i) => (
              <li key={i} className="flex items-start gap-3">
                <span className="flex-shrink-0 font-mono text-[10px] text-cyan mt-0.5">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span className="text-[12px] text-text-secondary">{step}</span>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </div>
  )
}
