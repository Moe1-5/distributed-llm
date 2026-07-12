/**
 * Settings.tsx
 * Application settings page.
 *
 * Currently manages:
 *   - Optional HuggingFace token diagnostics/fallback settings.
 */

import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

type SaveState = 'idle' | 'saving' | 'saved' | 'error' | 'deleting'

export default function Settings(): React.JSX.Element {
  const [tokenInput, setTokenInput] = useState('')
  const [tokenPreview, setTokenPreview] = useState<string | null>(null)
  const [tokenSet, setTokenSet] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

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

      <div className="flex flex-col gap-6 p-7 max-w-2xl">
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
            <label className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
              {tokenSet ? 'Replace Token' : 'Enter Token'}
            </label>
            <div className="flex gap-2">
              <input
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
