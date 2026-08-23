/**
 * Chat.tsx
 * Real-time token streaming chat interface.
 *
 * WebSocket lifecycle:
 *   - Does NOT send while connecting — user opens the stream before prompting
 *   - Reconnects automatically if connection drops between messages
 *   - Cleans up on unmount
 */

import React, { useState, useRef, useEffect, useCallback } from 'react'
import { applyIndependently } from '../api/independentRefresh'
import {
  api,
  createStreamSocket,
  type GenerationTraceResult,
  type GeneratorStatus,
  type NetworkRuntimeSnapshot
} from '../api/client'
import { recordDiagnostic } from '../api/diagnostics'
import { runtimeStages } from '../api/presentationState'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  nodeTrace?: string[]
  streaming?: boolean
  error?: boolean
}

type ConnectionState = 'closed' | 'connecting' | 'open' | 'error'
type BackendState = 'checking' | 'online' | 'offline'
type DiagnosticState = 'idle' | 'queued' | 'running' | 'ready' | 'failed' | 'cancelled'

const TRACE_JOB_DEADLINE_MS = 180_000

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function makeMessage(
  role: 'user' | 'assistant',
  content: string,
  extra: Partial<Message> = {}
): Message {
  return { id: makeId(), role, content, timestamp: new Date(), ...extra }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Chat(): React.JSX.Element {
  const [messages, setMessages] = useState<Message[]>([
    makeMessage('assistant', 'Waiting for generator route.')
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [traceLoading, setTraceLoading] = useState(false)
  const [connState, setConnState] = useState<ConnectionState>('closed')
  const [backendState, setBackendState] = useState<BackendState>('checking')
  const [generatorStatus, setGeneratorStatus] = useState<GeneratorStatus | null>(null)
  const [networkState, setNetworkState] = useState<NetworkRuntimeSnapshot | null>(null)
  const [readinessError, setReadinessError] = useState<string | null>(null)
  const [diagnosticState, setDiagnosticState] = useState<DiagnosticState>('idle')
  const [traceProgress, setTraceProgress] = useState<string | null>(null)

  const bottomRef = useRef<HTMLDivElement>(null)
  const socketRef = useRef<ReturnType<typeof createStreamSocket> | null>(null)
  const mountedRef = useRef(true)
  const readinessIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const readinessInFlightRef = useRef(false)

  // ---------------------------------------------------------------------------
  // Cleanup on unmount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      socketRef.current?.close()
      socketRef.current = null
      if (readinessIntervalRef.current) clearInterval(readinessIntervalRef.current)
    }
  }, [])

  const refreshReadiness = useCallback(async () => {
    if (readinessInFlightRef.current) return
    readinessInFlightRef.current = true
    try {
      const statusRequest = applyIndependently(
        api.getStatus(),
        (status) => {
          if (!mountedRef.current) return
          setBackendState('online')
          setNetworkState(status.network ?? null)
          setReadinessError(null)
        },
        (err) => {
          if (!mountedRef.current) return
          setBackendState('offline')
          setNetworkState(null)
          setReadinessError(err instanceof Error ? err.message : 'Backend readiness check failed')
        }
      )
      const generatorRequest = applyIndependently(
        api.getGeneratorStatus(),
        (generator) => {
          if (!mountedRef.current) return
          setGeneratorStatus(generator)
        },
        (err) => {
          if (!mountedRef.current) return
          setGeneratorStatus(null)
          setReadinessError(err instanceof Error ? err.message : 'Generator readiness check failed')
        }
      )
      await Promise.allSettled([statusRequest, generatorRequest])
    } finally {
      readinessInFlightRef.current = false
    }
  }, [])

  useEffect(() => {
    void refreshReadiness()
    readinessIntervalRef.current = setInterval(refreshReadiness, 5_000)

    return () => {
      if (readinessIntervalRef.current) clearInterval(readinessIntervalRef.current)
    }
  }, [refreshReadiness])

  useEffect(() => {
    setMessages((previous) => {
      const first = previous[0]
      if (
        !first ||
        first.role !== 'assistant' ||
        ![
          'Waiting for generator route.',
          'Generator route is ready. Open the stream to start inference.'
        ].includes(first.content)
      ) {
        return previous
      }
      const content =
        generatorStatus?.ready && generatorStatus.route_ready
          ? 'Generator route is ready. Open the stream to start inference.'
          : 'Waiting for generator route.'
      return content === first.content
        ? previous
        : [{ ...first, content, timestamp: new Date() }, ...previous.slice(1)]
    })
  }, [generatorStatus?.ready, generatorStatus?.route_ready])

  // ---------------------------------------------------------------------------
  // Auto-scroll to bottom when messages update
  // ---------------------------------------------------------------------------

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // ---------------------------------------------------------------------------
  // Append a token to the last streaming assistant message
  // ---------------------------------------------------------------------------

  const appendToken = useCallback((token: string) => {
    if (!mountedRef.current) return
    setMessages((prev) => {
      const last = prev[prev.length - 1]
      if (last?.role === 'assistant' && last.streaming) {
        return [...prev.slice(0, -1), { ...last, content: last.content + token }]
      }
      return prev
    })
  }, [])

  // ---------------------------------------------------------------------------
  // Finalise streaming message when done
  // ---------------------------------------------------------------------------

  const finaliseMessage = useCallback(
    (trace: string[]) => {
      if (!mountedRef.current) return
      recordDiagnostic({
        source: 'inference',
        severity: 'success',
        summary: 'Generation stream completed.',
        details: { route: trace }
      })
      setMessages((prev) => {
        const last = prev[prev.length - 1]
        if (last?.role === 'assistant' && last.streaming) {
          return [...prev.slice(0, -1), { ...last, streaming: false, nodeTrace: trace }]
        }
        return prev
      })
      setLoading(false)
      setConnState('open')
      void refreshReadiness()
    },
    [refreshReadiness]
  )

  // ---------------------------------------------------------------------------
  // Handle WS error
  // ---------------------------------------------------------------------------

  const handleError = useCallback(
    (error: string) => {
      if (!mountedRef.current) return
      recordDiagnostic({
        source: 'inference',
        severity: 'error',
        summary: error,
        details: {
          stage: 'generation',
          model_name: generatorStatus?.model_name ?? null,
          route: generatorStatus?.node_trace ?? []
        }
      })
      setMessages((prev) => {
        // Replace streaming placeholder with error message if present
        const last = prev[prev.length - 1]
        if (last?.role === 'assistant' && last.streaming) {
          return [
            ...prev.slice(0, -1),
            { ...last, content: `Error: ${error}`, streaming: false, error: true }
          ]
        }
        return [...prev, makeMessage('assistant', `Error: ${error}`, { error: true })]
      })
      setLoading(false)
      setConnState('error')
      socketRef.current?.close()
      socketRef.current = null
    },
    [generatorStatus?.model_name, generatorStatus?.node_trace]
  )

  const handleSocketOpen = useCallback(() => {
    if (!mountedRef.current) return
    setConnState('open')
  }, [])

  const handleSocketClose = useCallback(() => {
    if (!mountedRef.current) return
    socketRef.current = null
    setConnState((prev) => (prev === 'error' ? prev : 'closed'))
    setLoading(false)
  }, [])

  // ---------------------------------------------------------------------------
  // Connect (or reconnect) WebSocket.
  // ---------------------------------------------------------------------------

  const connectStream = useCallback(() => {
    if (socketRef.current !== null || connState === 'connecting') return
    if (!generatorStatus?.ready || !generatorStatus.route_ready) return

    setConnState('connecting')

    try {
      socketRef.current = createStreamSocket(
        appendToken,
        finaliseMessage,
        handleError,
        handleSocketOpen,
        handleSocketClose
      )
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error'
      recordDiagnostic({
        source: 'inference',
        severity: 'error',
        summary: `Failed to create generation WebSocket: ${msg}`,
        details: { stage: 'websocket_connect' }
      })
      setConnState('error')
      setMessages((prev) => [
        ...prev,
        makeMessage('assistant', `Failed to connect to backend: ${msg}`, { error: true })
      ])
    }
  }, [
    appendToken,
    connState,
    finaliseMessage,
    generatorStatus?.ready,
    generatorStatus?.route_ready,
    handleError,
    handleSocketClose,
    handleSocketOpen
  ])

  // ---------------------------------------------------------------------------
  // Send message
  // ---------------------------------------------------------------------------

  const handleSend = useCallback(() => {
    const text = input.trim()
    if (
      !text ||
      loading ||
      connState !== 'open' ||
      !generatorStatus?.ready ||
      !generatorStatus.route_ready
    )
      return
    if (socketRef.current === null) return

    // Add user message
    setMessages((prev) => [...prev, makeMessage('user', text)])

    // Add empty assistant placeholder that fills as tokens arrive
    setMessages((prev) => [...prev, makeMessage('assistant', '', { streaming: true })])

    setInput('')
    setLoading(true)
    socketRef.current.send(text)
  }, [connState, generatorStatus?.ready, generatorStatus?.route_ready, input, loading])

  const handleStop = useCallback(() => {
    if (!loading) return

    void api.stopGenerator().catch(() => undefined)
    socketRef.current?.close()
    socketRef.current = null
    setMessages((prev) => {
      const last = prev[prev.length - 1]
      if (last?.role === 'assistant' && last.streaming) {
        return [
          ...prev.slice(0, -1),
          {
            ...last,
            content: last.content ? `${last.content}\n\nStopped.` : 'Stopped.',
            streaming: false
          }
        ]
      }
      return prev
    })
    setLoading(false)
    setConnState('closed')
    void refreshReadiness()
  }, [loading, refreshReadiness])

  const handleTrace = useCallback(async () => {
    const text = input.trim()
    if (!text || loading || traceLoading || !generatorStatus?.ready || !generatorStatus.route_ready)
      return

    setTraceLoading(true)
    setDiagnosticState('queued')
    setTraceProgress('Submitting a separate legacy-only token trace diagnostic.')
    socketRef.current?.close()
    socketRef.current = null
    setConnState('closed')
    try {
      let job = await api.traceGenerationAsync(text, {
        maxNewTokens: 8,
        topK: 0,
        repetitionPenalty: 1,
        doSample: false
      })
      const deadline = Date.now() + TRACE_JOB_DEADLINE_MS
      while (job.status === 'queued' || job.status === 'running') {
        if (!mountedRef.current) return
        if (Date.now() >= deadline) {
          await api.cancelLifecycleJob(job.job_id).catch(() => undefined)
          throw new Error(
            'Legacy trace exceeded its three-minute diagnostic deadline; cancellation was requested.'
          )
        }
        setDiagnosticState(job.status)
        setTraceProgress(`${job.detail} (${job.elapsed_seconds.toFixed(1)} seconds)`)
        await new Promise((resolve) => window.setTimeout(resolve, 750))
        job = await api.getLifecycleJob(job.job_id)
      }
      setDiagnosticState(job.status)
      if (job.status !== 'ready') {
        throw new Error(job.error ?? job.detail ?? `Legacy trace ${job.status}`)
      }
      const trace = job.result?.trace as GenerationTraceResult | undefined
      if (!trace || typeof trace.trace_id !== 'string') {
        throw new Error('Legacy trace job completed without a valid trace artifact.')
      }
      const firstStep = trace.steps[0]
      const firstToken = firstStep
        ? `${firstStep.token_text || '(empty)'} [${firstStep.token_id}]`
        : 'none'
      const topCandidates =
        firstStep?.top_candidates
          .map((candidate) => {
            const token = candidate.token_text || '(empty)'
            return `${token} [${candidate.token_id}] ${candidate.logit.toFixed(3)}`
          })
          .join(', ') || 'none'
      const result = [
        `Trace ${trace.trace_id}`,
        `Model: ${trace.model_name}`,
        `Steps: ${trace.steps.length}`,
        `Greedy response: ${trace.response || '(empty)'}`,
        `First token: ${firstToken}`,
        `First-step candidates: ${topCandidates}`,
        `Replacement chars: ${trace.response_contains_replacement_char ? 'yes' : 'no'}`,
        `Trace file: ${trace.trace_file}`
      ].join('\n')

      setMessages((prev) => [
        ...prev,
        makeMessage('user', text),
        makeMessage('assistant', result, { nodeTrace: trace.node_trace })
      ])
      setInput('')
      setDiagnosticState('ready')
      setTraceProgress(`Legacy trace ${trace.trace_id} completed and was saved.`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Trace request failed'
      setDiagnosticState('failed')
      setTraceProgress(msg)
      recordDiagnostic({
        source: 'inference',
        severity: 'error',
        summary: msg,
        details: { stage: 'legacy_trace' }
      })
      setMessages((prev) => [...prev, makeMessage('assistant', `Error: ${msg}`, { error: true })])
    } finally {
      setTraceLoading(false)
      void refreshReadiness()
    }
  }, [
    generatorStatus?.ready,
    generatorStatus?.route_ready,
    input,
    loading,
    refreshReadiness,
    traceLoading
  ])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // ---------------------------------------------------------------------------
  // Derived state for UI
  // ---------------------------------------------------------------------------

  const generatorReady = Boolean(generatorStatus?.ready)
  const routeReady = Boolean(generatorStatus?.route_ready)
  const canConnect =
    backendState === 'online' &&
    generatorReady &&
    routeReady &&
    (connState === 'closed' || connState === 'error')
  const canSend =
    Boolean(input.trim()) &&
    !loading &&
    backendState === 'online' &&
    connState === 'open' &&
    generatorReady &&
    routeReady
  const canTrace =
    Boolean(input.trim()) &&
    !loading &&
    !traceLoading &&
    backendState === 'online' &&
    generatorReady &&
    routeReady
  const inputPlaceholder = loading
    ? 'Generating...'
    : traceLoading
      ? 'Tracing...'
      : !generatorReady || !routeReady
        ? 'Generator route not ready'
        : connState !== 'open'
          ? 'Stream closed'
          : 'Send a message...'

  const readinessItems = runtimeStages({
    backend: backendState,
    network: networkState,
    generator: generatorStatus,
    stream: connState,
    generationActive: loading,
    diagnostics: diagnosticState
  })
  const recovery = readinessItems.find((item) => item.tone === 'failed' && item.action)
  const primaryActionReason = loading
    ? null
    : backendState !== 'online'
      ? 'Chat unavailable: restart the managed backend from Settings.'
      : !generatorReady || !routeReady
        ? `Chat unavailable: ${generatorStatus?.reasons.join('; ') || 'start and validate an inference route on Network.'}`
        : connState === 'connecting'
          ? 'Chat unavailable while the local generation stream is opening.'
          : null

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex flex-shrink-0 flex-col gap-3 border-b border-border px-7 py-5 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">Inference</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            Distributed P2P token generation
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {readinessItems.map((item) => (
            <span
              key={item.id}
              title={`${item.explanation}${item.action ? ` Next: ${item.action}` : ''}`}
              aria-label={`${item.label}: ${item.value}. ${item.explanation}`}
              className={`
                flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[9px] font-semibold
                ${
                  item.tone === 'ready'
                    ? 'border-green/20 bg-green/5 text-green'
                    : item.tone === 'failed'
                      ? 'border-red/20 bg-red/5 text-red'
                      : item.tone === 'degraded'
                        ? 'border-amber/30 bg-amber/10 text-amber'
                        : item.tone === 'working'
                          ? 'border-cyan/20 bg-cyan-dim text-cyan'
                          : 'border-border bg-bg-surface text-text-dim'
                }
              `}
            >
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full ${
                  item.tone === 'ready'
                    ? 'bg-green shadow-[0_0_6px_#00ff88]'
                    : item.tone === 'failed'
                      ? 'bg-red'
                      : item.tone === 'working'
                        ? 'bg-cyan animate-pulse'
                        : 'bg-text-dim'
                }`}
              />
              {item.label}: {item.value}
            </span>
          ))}
        </div>
      </div>

      {(readinessError || (generatorStatus?.reasons.length ?? 0) > 0) && (
        <div className="flex-shrink-0 border-b border-red/20 bg-red/5 px-7 py-3">
          {readinessError && <p className="font-mono text-[11px] text-red">{readinessError}</p>}
          {generatorStatus?.reasons.map((reason) => (
            <p key={reason} className="font-mono text-[11px] text-red">
              {reason}
            </p>
          ))}
        </div>
      )}

      {(recovery || traceProgress) && (
        <div className="flex flex-shrink-0 flex-col gap-1 border-b border-border bg-bg-surface px-7 py-2 font-mono text-[10px]">
          {recovery && (
            <p className="text-red">
              {recovery.label}: {recovery.explanation} Next: {recovery.action}
            </p>
          )}
          {traceProgress && (
            <p className={diagnosticState === 'failed' ? 'text-red' : 'text-amber'}>
              Legacy trace: {traceProgress}
            </p>
          )}
        </div>
      )}

      {/* Messages */}
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-7 py-6">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`animate-fade-up flex max-w-[75%] flex-col gap-1.5 ${
              msg.role === 'user' ? 'self-end items-end' : 'self-start items-start'
            }`}
          >
            {/* Role + timestamp */}
            <div className="flex items-center gap-2 font-mono text-[9px] tracking-widest text-text-dim">
              <span>{msg.role === 'user' ? 'YOU' : 'MODEL'}</span>
              <span>
                {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
              {msg.streaming && <span className="text-cyan animate-blink">● STREAMING</span>}
            </div>

            {/* Bubble */}
            <div
              className={`whitespace-pre-wrap rounded-xl border px-4 py-3 text-[14px] leading-relaxed ${
                msg.error
                  ? 'rounded-bl-sm border-red/20 bg-red/5 text-red'
                  : msg.role === 'user'
                    ? 'rounded-br-sm border-cyan/20 bg-cyan-dim text-text-primary'
                    : 'rounded-bl-sm border-border bg-bg-elevated text-text-primary'
              }`}
            >
              {msg.content ||
                (msg.streaming && (
                  <span className="flex gap-1">
                    {[0, 1, 2].map((i) => (
                      <span
                        key={i}
                        className="inline-block h-1.5 w-1.5 rounded-full bg-text-secondary animate-blink"
                        style={{ animationDelay: `${i * 0.2}s` }}
                      />
                    ))}
                  </span>
                ))}
            </div>

            {/* Node trace */}
            {msg.nodeTrace && msg.nodeTrace.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {msg.nodeTrace.map((t, i) => (
                  <span
                    key={i}
                    className="rounded border border-border bg-bg-elevated px-1.5 py-0.5 font-mono text-[9px] text-text-dim"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex flex-shrink-0 flex-col gap-2 border-t border-border bg-bg-surface px-7 py-4">
        {primaryActionReason && (
          <p className="font-mono text-[10px] text-amber">{primaryActionReason}</p>
        )}
        <div className="flex items-end gap-3">
          <textarea
            aria-label="Inference prompt"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading || traceLoading}
            placeholder={inputPlaceholder}
            rows={1}
            className="
            flex-1 resize-none rounded-xl border border-border-bright bg-bg-elevated
            px-4 py-3 text-[14px] leading-relaxed text-text-primary outline-none
            placeholder:text-text-dim focus:border-cyan/40 transition-colors duration-150
            min-h-[46px] max-h-[140px] disabled:opacity-50
          "
          />
          <button
            type="button"
            aria-label="Run legacy-only token trace diagnostic"
            onClick={handleTrace}
            disabled={!canTrace}
            className={`
            flex h-[46px] w-[96px] flex-shrink-0 items-center justify-center
            rounded-xl border font-mono text-[11px] font-semibold transition-all duration-150
            ${
              canTrace
                ? 'cursor-pointer border-amber/30 bg-amber/10 text-amber hover:bg-amber/20'
                : 'cursor-not-allowed border-border bg-bg-elevated text-text-dim opacity-50'
            }
          `}
            title="Run a separate legacy-only token trace. This closes the chat stream first and does not test the receipt/session path."
          >
            {traceLoading ? '...' : 'LEGACY TRACE'}
          </button>
          <button
            type="button"
            aria-label={
              loading
                ? 'Stop inference'
                : connState === 'open'
                  ? 'Send message'
                  : 'Open generation stream'
            }
            onClick={loading ? handleStop : connState === 'open' ? handleSend : connectStream}
            disabled={!loading && (connState === 'open' ? !canSend : !canConnect)}
            className={`
            flex h-[46px] flex-shrink-0 items-center justify-center
            rounded-xl border font-mono font-semibold text-cyan
            transition-all duration-150
            ${
              loading
                ? 'w-[74px] cursor-pointer border-red/30 bg-red/10 text-[11px] text-red hover:bg-red/20'
                : connState !== 'open'
                  ? canConnect
                    ? 'w-[74px] cursor-pointer border-cyan/30 bg-cyan-dim text-[11px] hover:bg-cyan/20'
                    : 'w-[74px] cursor-not-allowed border-border bg-bg-elevated text-[11px] text-text-dim opacity-50'
                  : canSend
                    ? 'w-[46px] cursor-pointer border-cyan/30 bg-cyan-dim text-xl hover:bg-cyan/20'
                    : 'w-[46px] cursor-not-allowed border-border bg-bg-elevated text-xl text-text-dim opacity-50'
            }
          `}
            title={
              loading ? 'Stop inference' : connState === 'open' ? 'Send message' : 'Open stream'
            }
          >
            {loading ? 'STOP' : connState === 'open' ? '↑' : 'OPEN'}
          </button>
        </div>
      </div>
    </div>
  )
}
