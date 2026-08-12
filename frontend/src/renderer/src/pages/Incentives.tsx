import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { IncentiveAccounting } from '../api/client'

function compactKey(value: string): string {
  return value.length > 24 ? `${value.slice(0, 12)}...${value.slice(-8)}` : value
}

export default function Incentives(): React.JSX.Element {
  const [accounting, setAccounting] = useState<IncentiveAccounting | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setAccounting(await api.getIncentiveAccounting())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Incentive accounting is unavailable')
    }
  }, [])

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
    }
  }, [refresh])

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="border-b border-border px-7 py-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-text-primary">Incentives</h1>
            <p className="mt-0.5 font-mono text-[11px] text-text-secondary">
              Verified useful-work accounting
            </p>
          </div>
          <span
            className={`rounded border px-2.5 py-1 font-mono text-[10px] uppercase ${
              accounting?.mode === 'credit'
                ? 'border-green/30 bg-green/10 text-green'
                : accounting?.mode === 'shadow'
                  ? 'border-amber/30 bg-amber/10 text-amber'
                  : 'border-border bg-bg-surface text-text-dim'
            }`}
          >
            {accounting?.mode ?? 'offline'}
          </span>
        </div>
      </header>

      {error && (
        <div className="border-b border-red/20 bg-red/5 px-7 py-3 font-mono text-[11px] text-red">
          {error}
        </div>
      )}

      <section className="grid grid-cols-4 border-b border-border">
        {[
          ['Verified credits', accounting?.account.verified_credits ?? 0],
          ['Pending receipts', accounting?.submission.pending ?? 0],
          ['Accepted receipts', accounting?.submission.accepted ?? 0],
          ['Rejected receipts', accounting?.submission.rejected ?? 0]
        ].map(([label, value], index) => (
          <div key={label} className={`px-6 py-5 ${index > 0 ? 'border-l border-border' : ''}`}>
            <p className="font-mono text-[9px] uppercase text-text-dim">{label}</p>
            <p className="mt-2 font-mono text-2xl text-text-primary">{value}</p>
          </div>
        ))}
      </section>

      <section className="border-b border-border px-7 py-5">
        <div className="grid grid-cols-[160px_1fr] gap-y-3 text-[12px]">
          <span className="font-mono text-[10px] uppercase text-text-dim">
            Application identity
          </span>
          <span className="font-mono text-text-secondary" title={accounting?.identity.public_key}>
            {accounting ? compactKey(accounting.identity.public_key) : 'Unavailable'}
          </span>
          <span className="font-mono text-[10px] uppercase text-text-dim">Settlement</span>
          <span
            className={accounting?.submission.settlement_connected ? 'text-green' : 'text-text-dim'}
          >
            {accounting?.submission.settlement_connected
              ? 'Connected'
              : accounting?.settlement_url
                ? 'Disconnected'
                : 'Not configured'}
          </span>
          <span className="font-mono text-[10px] uppercase text-text-dim">Ledger entries</span>
          <span className="text-text-secondary">{accounting?.account.entry_count ?? 0}</span>
        </div>
        {accounting?.submission.last_error && (
          <p className="mt-4 border-l-2 border-red/40 pl-3 font-mono text-[10px] text-red">
            {accounting.submission.last_error}
          </p>
        )}
      </section>

      <section className="px-7 py-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">Local useful work</h2>
          <span className="font-mono text-[9px] uppercase text-text-dim">
            Selected successful RPC work only
          </span>
        </div>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full table-fixed text-left text-[11px]">
            <thead className="border-b border-border bg-bg-surface font-mono text-[9px] uppercase text-text-dim">
              <tr>
                <th className="px-3 py-2">Model</th>
                <th className="w-24 px-3 py-2">Layers</th>
                <th className="w-24 px-3 py-2">Requests</th>
                <th className="w-28 px-3 py-2">Positions</th>
                <th className="w-24 px-3 py-2">Failures</th>
              </tr>
            </thead>
            <tbody>
              {(accounting?.local_contributions ?? []).map((item, index) => (
                <tr
                  key={`${item.peer_id ?? index}-${item.layer_start}`}
                  className="border-b border-border last:border-b-0"
                >
                  <td className="truncate px-3 py-2.5 text-text-secondary" title={item.model_name}>
                    {item.model_name}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-text-secondary">
                    {item.layer_start}-{item.layer_end}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-text-primary">
                    {item.requests_served}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-text-primary">
                    {item.token_positions_served ?? 0}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-text-primary">
                    {item.failed_requests ?? 0}
                  </td>
                </tr>
              ))}
              {(accounting?.local_contributions.length ?? 0) === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-3 py-8 text-center font-mono text-[10px] text-text-dim"
                  >
                    No local serving activity
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
