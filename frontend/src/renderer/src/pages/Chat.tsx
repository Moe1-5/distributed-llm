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
import { api, createStreamSocket, type GeneratorStatus } from '../api/client'

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
  const [readinessError, setReadinessError] = useState<string | null>(null)

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
      const statusRequest = applyIndependently(api.getStatus(), () => {
        if (!mountedRef.current) return
        setBackendState('online')
        setReadinessError(null)
      }, (err) => {
        if (!mountedRef.current) return
        setBackendState('offline')
        setReadinessError(err instanceof Error ? err.message : 'Backend readiness check failed')
      })
      const generatorRequest = applyIndependently(api.getGeneratorStatus(), (generator) => {
        if (!mountedRef.current) return
        setGeneratorStatus(generator)
      }, (err) => {
        if (!mountedRef.current) return
        setGeneratorStatus(null)
        setReadinessError(err instanceof Error ? err.message : 'Generator readiness check failed')
      })
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

  const finaliseMessage = useCallback((trace: string[]) => {
    if (!mountedRef.current) return
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
  }, [refreshReadiness])

  // ---------------------------------------------------------------------------
  // Handle WS error
  // ---------------------------------------------------------------------------

  const handleError = useCallback((error: string) => {
    if (!mountedRef.current) return
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
  }, [])

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
    if (!text || loading || connState !== 'open' || !generatorStatus?.ready || !generatorStatus.route_ready) return
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
    if (!text || loading || traceLoading || !generatorStatus?.ready || !generatorStatus.route_ready) return

    setTraceLoading(true)
    try {
      const trace = await api.traceGeneration(text, {
        maxNewTokens: 8,
        topK: 0,
        repetitionPenalty: 1,
        doSample: false
      })
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
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Trace request failed'
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

  const statusLabel = {
    closed: 'CLOSED',
    connecting: 'CONNECTING',
    open: 'OPEN',
    error: 'ERROR'
  }[connState]

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

  const readinessItems = [
    {
      label: 'Backend',
      value:
        backendState === 'checking' ? 'CHECKING' : backendState === 'online' ? 'ONLINE' : 'OFFLINE',
      ok: backendState === 'online'
    },
    {
      label: 'WebSocket',
      value: statusLabel,
      ok: connState === 'open'
    },
    {
      label: 'Generator',
      value: generatorReady ? 'READY' : 'WAITING',
      ok: generatorReady
    },
    {
      label: 'Route',
      value: routeReady ? 'READY' : 'WAITING',
      ok: routeReady
    }
  ]

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-7 py-5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">Inference</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            Distributed P2P token generation
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {readinessItems.map((item) => (
            <span
              key={item.label}
              className={`
                flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[9px] font-semibold
                ${
                  item.ok
                    ? 'border-green/20 bg-green/5 text-green'
                    : 'border-border bg-bg-surface text-text-dim'
                }
              `}
            >
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full ${
                  item.ok ? 'bg-green shadow-[0_0_6px_#00ff88]' : 'bg-text-dim'
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
      <div className="flex flex-shrink-0 items-end gap-3 border-t border-border bg-bg-surface px-7 py-4">
        <textarea
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
          onClick={handleTrace}
          disabled={!canTrace}
          className={`
            flex h-[46px] w-[68px] flex-shrink-0 items-center justify-center
            rounded-xl border font-mono text-[11px] font-semibold transition-all duration-150
            ${
              canTrace
                ? 'cursor-pointer border-amber/30 bg-amber/10 text-amber hover:bg-amber/20'
                : 'cursor-not-allowed border-border bg-bg-elevated text-text-dim opacity-50'
            }
          `}
          title="Run token trace"
        >
          {traceLoading ? '...' : 'TRACE'}
        </button>
        <button
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
          title={loading ? 'Stop inference' : connState === 'open' ? 'Send message' : 'Open stream'}
        >
          {loading ? 'STOP' : connState === 'open' ? '↑' : 'OPEN'}
        </button>
      </div>
    </div>
  )
}
