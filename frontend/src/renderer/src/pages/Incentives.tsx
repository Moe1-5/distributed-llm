import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, type IncentivesStatus } from '../api/client'

function shortIdentity(value: string | null): string {
  if (!value) return 'Not initialized'
  return value.length > 28 ? `${value.slice(0, 14)}...${value.slice(-10)}` : value
}

function connectivityTone(status: IncentivesStatus['settlement_connectivity']): string {
  if (status === 'connected') return 'text-green'
  if (status === 'error') return 'text-red'
  if (status === 'unconfigured') return 'text-amber'
  return 'text-text-secondary'
}

export default function Incentives(): React.JSX.Element {
  const [status, setStatus] = useState<IncentivesStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const next = await api.getIncentives()
      setStatus(next)
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
                rejected
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
            <p className="mt-3 rounded-lg border border-red/20 bg-red/5 px-4 py-3 font-mono text-[11px] text-red">
              {status.last_error}
            </p>
          )}
        </section>
      </div>
    </div>
  )
}
