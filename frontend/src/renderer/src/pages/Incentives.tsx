import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type DeveloperAccessStatus,
  type IncentivesStatus
} from '../api/client'

function shortIdentity(value: string | null): string {
  if (!value) return 'Not initialized'
  return value.length > 28 ? `${value.slice(0, 14)}...${value.slice(-10)}` : value
}

function connectivityTone(status: IncentivesStatus['settlement_connectivity']): string {
  if (status === 'connected') return 'text-green'
  if (status === 'error') return 'text-red'
  if (status === 'unconfigured' || status === 'retrying') return 'text-amber'
  return 'text-text-secondary'
}

export default function Incentives(): React.JSX.Element {
  const [status, setStatus] = useState<IncentivesStatus | null>(null)
  const [access, setAccess] = useState<DeveloperAccessStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [keyName, setKeyName] = useState('Local integration')
  const [newApiKey, setNewApiKey] = useState<string | null>(null)
  const [keyAction, setKeyAction] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [next, developerAccess] = await Promise.all([
        api.getIncentives(),
        api.getDeveloperAccess()
      ])
      setStatus(next)
      setAccess(developerAccess)
      setError(null)
      setLastUpdated(new Date())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Incentive status refresh failed')
    }
  }, [])

  useEffect(() => {
    const initialRefresh = setTimeout(() => void refresh(), 0)
    intervalRef.current = setInterval(refresh, 10_000)
    return () => {
      clearTimeout(initialRefresh)
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [refresh])

  const mode = status?.mode ?? 'off'

  const createApiKey = useCallback(async () => {
    if (keyAction) return
    setKeyAction(true)
    try {
      const created = await api.createDeveloperApiKey(keyName)
      setNewApiKey(created.api_key)
      setError(null)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'API key creation failed')
    } finally {
      setKeyAction(false)
    }
  }, [keyAction, keyName, refresh])

  const revokeApiKey = useCallback(
    async (keyId: string) => {
      if (keyAction || !window.confirm('Revoke this developer API key?')) return
      setKeyAction(true)
      try {
        await api.revokeDeveloperApiKey(keyId)
        await refresh()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : 'API key revocation failed')
      } finally {
        setKeyAction(false)
      }
    },
    [keyAction, refresh]
  )

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="flex flex-shrink-0 items-center justify-between border-b border-border px-7 py-5">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Incentives</h1>
          <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
            Useful-work receipts and verified accounting
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="font-mono text-[10px] text-text-dim">
              {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <button
            type="button"
            onClick={() => void refresh()}
            className="h-9 rounded-lg border border-cyan/30 bg-cyan-dim px-4 font-mono text-[10px] font-semibold text-cyan hover:bg-cyan/20"
          >
            REFRESH
          </button>
        </div>
      </header>

      <div className="flex flex-col gap-6 p-7">
        {error && (
          <div className="rounded-lg border border-red/20 bg-red/5 px-5 py-4 font-mono text-[12px] text-red">
            {error}
          </div>
        )}

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['Verified credits', status?.verified_credits ?? 0],
            ['Useful positions', status?.useful_positions_served ?? 0],
            ['Accepted receipts', status?.accepted_receipts ?? 0],
            ['Ledger entries', status?.ledger_entries ?? 0]
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-border bg-bg-elevated p-4">
              <p className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                {label}
              </p>
              <p className="mt-2 text-2xl font-semibold tabular-nums text-text-primary">{value}</p>
            </div>
          ))}
        </section>

        <section className="border-y border-border py-5">
          <div className="mb-4 flex items-center justify-between gap-4">
            <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
              Receipt service
            </h2>
            <span className="rounded border border-border-bright bg-bg-surface px-2 py-1 font-mono text-[10px] font-semibold text-text-primary uppercase">
              {mode}
            </span>
          </div>
          <dl className="grid gap-x-8 gap-y-4 md:grid-cols-2">
            <div>
              <dt className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                Settlement
              </dt>
              <dd
                className={`mt-1 font-mono text-[12px] font-semibold uppercase ${connectivityTone(status?.settlement_connectivity ?? 'disabled')}`}
              >
                {status?.settlement_connectivity ?? 'disabled'}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                Protocol
              </dt>
              <dd className="mt-1 font-mono text-[12px] text-text-primary">
                Version {status?.protocol_version ?? 1}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                Pending submissions
              </dt>
              <dd className="mt-1 font-mono text-[12px] tabular-nums text-text-primary">
                {status?.pending_submissions ?? 0}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                Local submission results
              </dt>
              <dd className="mt-1 font-mono text-[12px] text-text-primary">
                {status?.accepted_submissions ?? 0} accepted, {status?.rejected_submissions ?? 0}{' '}
                rejected, {status?.submission_retry_attempts ?? 0} retries
              </dd>
            </div>
          </dl>
        </section>

        <section>
          <h2 className="mb-3 font-mono text-[10px] tracking-widest text-text-dim uppercase">
            Node identity
          </h2>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="min-w-0 rounded-lg border border-border bg-bg-elevated p-4">
              <p className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                Application public key
              </p>
              <p
                className="mt-2 break-all font-mono text-[12px] text-text-primary"
                title={status?.application_public_key ?? ''}
              >
                {shortIdentity(status?.application_public_key ?? null)}
              </p>
            </div>
            <div className="min-w-0 rounded-lg border border-border bg-bg-elevated p-4">
              <p className="font-mono text-[9px] tracking-widest text-text-dim uppercase">
                Current p2p peer
              </p>
              <p
                className="mt-2 break-all font-mono text-[12px] text-text-primary"
                title={status?.p2p_peer_id ?? ''}
              >
                {shortIdentity(status?.p2p_peer_id ?? null)}
              </p>
            </div>
          </div>
          {status?.last_error && (
            <p
              className={`mt-3 rounded-lg px-4 py-3 font-mono text-[11px] ${
                status.settlement_connectivity === 'retrying'
                  ? 'border border-amber/20 bg-amber/5 text-amber'
                  : 'border border-red/20 bg-red/5 text-red'
              }`}
            >
              {status.last_error}
            </p>
          )}
        </section>

        <section className="border-t border-border pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-mono text-[10px] tracking-widest text-text-dim uppercase">
                Developer API
              </h2>
              <p className="mt-1 text-[12px] text-text-secondary">
                Free Electron chat remains available without an API key.
              </p>
            </div>
            <span className="rounded border border-border-bright px-2 py-1 font-mono text-[10px] text-text-primary uppercase">
              {access?.mode ?? 'off'}
            </span>
          </div>

          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 font-mono text-[10px] text-text-secondary">
            <span>Available: {access?.available_credits ?? 0}</span>
            <span>Reserved: {access?.reserved_credits ?? 0}</span>
            <span>Spent locally: {access?.spent_credits ?? 0}</span>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
            <input
              value={keyName}
              onChange={(event) => setKeyName(event.target.value)}
              maxLength={80}
              aria-label="API key name"
              className="h-10 min-w-0 rounded-lg border border-border-bright bg-bg-surface px-3 font-mono text-[12px] text-text-primary outline-none focus:border-cyan/40"
            />
            <button
              type="button"
              onClick={() => void createApiKey()}
              disabled={
                keyAction ||
                !access?.developer_api_enabled ||
                !access?.eligible_for_api_key ||
                !keyName.trim()
              }
              className="h-10 rounded-lg border border-cyan/30 bg-cyan-dim px-4 font-mono text-[10px] font-semibold text-cyan disabled:cursor-not-allowed disabled:opacity-40"
            >
              CREATE KEY
            </button>
          </div>

          {!access?.eligible_for_api_key && (
            <p className="mt-3 font-mono text-[10px] text-amber">
              A positive verified useful-work credit balance is required.
            </p>
          )}

          {newApiKey && (
            <div className="mt-4 rounded-lg border border-amber/30 bg-amber/5 p-4">
              <p className="font-mono text-[10px] font-semibold text-amber uppercase">
                API key shown once
              </p>
              <p className="mt-2 break-all font-mono text-[11px] text-text-primary">
                {newApiKey}
              </p>
              <button
                type="button"
                onClick={() => setNewApiKey(null)}
                className="mt-3 h-8 rounded border border-border-bright px-3 font-mono text-[10px] text-text-secondary"
              >
                DISMISS
              </button>
            </div>
          )}

          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {(access?.keys ?? []).map((key) => (
              <div key={key.key_id} className="rounded-lg border border-border bg-bg-elevated p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-[13px] font-medium text-text-primary">{key.name}</p>
                    <p className="mt-1 font-mono text-[10px] text-text-dim">
                      {key.key_prefix}...
                    </p>
                  </div>
                  <span
                    className={`font-mono text-[9px] font-semibold uppercase ${key.revoked_at ? 'text-red' : 'text-green'}`}
                  >
                    {key.revoked_at ? 'revoked' : 'active'}
                  </span>
                </div>
                {!key.revoked_at && (
                  <button
                    type="button"
                    onClick={() => void revokeApiKey(key.key_id)}
                    disabled={keyAction}
                    className="mt-4 h-8 rounded border border-red/30 px-3 font-mono text-[10px] text-red disabled:opacity-40"
                  >
                    REVOKE
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
