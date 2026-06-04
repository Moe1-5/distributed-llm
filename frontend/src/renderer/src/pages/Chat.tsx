/**
 * Chat.tsx
 * Real-time token streaming chat interface.
 *
 * WebSocket lifecycle:
 *   - Does NOT connect on mount — connects lazily on first message send
 *   - Reconnects automatically if connection drops between messages
 *   - Cleans up on unmount
 *
 * This avoids the race condition where the WS connects before the backend
 * has finished initialising its /stream handler.
 */

import React, { useState, useRef, useEffect, useCallback } from 'react'
import { createStreamSocket } from '../api/client'

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

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'error'

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
    makeMessage(
      'assistant',
      'Connected to backend. Start a node on the Network page to begin distributed inference.'
    )
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [connState, setConnState] = useState<ConnectionState>('idle')

  const bottomRef = useRef<HTMLDivElement>(null)
  const socketRef = useRef<ReturnType<typeof createStreamSocket> | null>(null)
  const mountedRef = useRef(true)

  // ---------------------------------------------------------------------------
  // Cleanup on unmount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [])

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
    setConnState('connected')
  }, [])

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
  }, [])

  // ---------------------------------------------------------------------------
  // Connect (or reconnect) WebSocket
  // Returns true if connection was established successfully
  // ---------------------------------------------------------------------------

  const ensureConnected = useCallback((): boolean => {
    // Reuse existing socket if still open
    if (socketRef.current !== null) {
      return true
    }

    setConnState('connecting')

    try {
      socketRef.current = createStreamSocket(appendToken, finaliseMessage, handleError)
      setConnState('connected')
      return true
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error'
      setConnState('error')
      setMessages((prev) => [
        ...prev,
        makeMessage('assistant', `Failed to connect to backend: ${msg}`, { error: true })
      ])
      return false
    }
  }, [appendToken, finaliseMessage, handleError])

  // ---------------------------------------------------------------------------
  // Send message
  // ---------------------------------------------------------------------------

  const handleSend = useCallback(() => {
    const text = input.trim()
    if (!text || loading) return

    // Connect lazily — first message triggers WS connection
    if (!ensureConnected()) return
    if (socketRef.current === null) return

    // Add user message
    setMessages((prev) => [...prev, makeMessage('user', text)])

    // Add empty assistant placeholder that fills as tokens arrive
    setMessages((prev) => [...prev, makeMessage('assistant', '', { streaming: true })])

    setInput('')
    setLoading(true)
    socketRef.current.send(text)
  }, [input, loading, ensureConnected])

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
    idle: 'READY',
    connecting: 'CONNECTING',
    connected: 'CONNECTED',
    error: 'ERROR'
  }[connState]

  const statusColor =
    connState === 'error'
      ? { badge: 'border-red/20 bg-red/5', dot: 'bg-red', text: 'text-red' }
      : connState === 'connecting'
        ? { badge: 'border-cyan/20 bg-cyan-dim', dot: 'bg-cyan animate-pulse', text: 'text-cyan' }
        : {
            badge: 'border-cyan/20 bg-cyan-dim',
            dot: 'bg-cyan shadow-[0_0_6px_#00d4ff]',
            text: 'text-cyan'
          }

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
        <div
          className={`flex items-center gap-2 rounded-full border px-3 py-1.5 ${statusColor.badge}`}
        >
          <span className={`inline-block h-1.5 w-1.5 rounded-full ${statusColor.dot}`} />
          <span
            className={`font-mono text-[10px] font-semibold tracking-wider ${statusColor.text}`}
          >
            {statusLabel}
          </span>
        </div>
      </div>

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
              className={`rounded-xl border px-4 py-3 text-[14px] leading-relaxed ${
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
          disabled={loading}
          placeholder={loading ? 'Generating...' : 'Send a message… (Enter to send)'}
          rows={1}
          className="
            flex-1 resize-none rounded-xl border border-border-bright bg-bg-elevated
            px-4 py-3 text-[14px] leading-relaxed text-text-primary outline-none
            placeholder:text-text-dim focus:border-cyan/40 transition-colors duration-150
            min-h-[46px] max-h-[140px] disabled:opacity-50
          "
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || loading}
          className={`
            flex h-[46px] w-[46px] flex-shrink-0 items-center justify-center
            rounded-xl border border-cyan/30 bg-cyan-dim text-xl text-cyan
            transition-all duration-150
            ${
              !input.trim() || loading
                ? 'cursor-not-allowed opacity-40'
                : 'cursor-pointer hover:bg-cyan/20'
            }
          `}
        >
          ↑
        </button>
      </div>
    </div>
  )
}
