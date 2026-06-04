export interface Node {
  id: string
  address: string
  layers: string
  status: 'online' | 'offline' | 'connecting'
  gpu: number
  cpu: number
  vram: string
  latency: number
}

interface NodeCardProps {
  node: Node
  onConnect: (id: string) => void
  onDisconnect: (id: string) => void
}

function StatBar({ value, color }: { value: number; color: 'cyan' | 'green' }): React.JSX.Element {
  return (
    <div className="h-1 flex-1 overflow-hidden rounded-full bg-border">
      <div
        className={`h-full rounded-full transition-all duration-500 ${
          color === 'cyan'
            ? 'bg-cyan shadow-[0_0_6px_#00d4ff66]'
            : 'bg-green shadow-[0_0_6px_#00ff8866]'
        }`}
        style={{ width: `${value}%` }}
      />
    </div>
  )
}

export default function NodeCard({
  node,
  onConnect,
  onDisconnect
}: NodeCardProps): React.JSX.Element {
  const isOnline = node.status === 'online'
  const isConnecting = node.status === 'connecting'

  const dotClass = isOnline
    ? 'bg-green shadow-[0_0_6px_#00ff88] animate-pulse-glow'
    : isConnecting
      ? 'bg-amber shadow-[0_0_6px_#ffaa00] animate-pulse-glow'
      : 'bg-red'

  const badgeClass = isOnline
    ? 'text-green border-green/30'
    : isConnecting
      ? 'text-amber border-amber/30'
      : 'text-red border-red/30'

  const statusLabel = isOnline ? 'ONLINE' : isConnecting ? 'CONNECTING' : 'OFFLINE'

  return (
    <div className="animate-fade-up flex flex-col gap-2 rounded-xl border border-border bg-bg-elevated p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`inline-block h-2 w-2 rounded-full flex-shrink-0 ${dotClass}`} />
          <span className="font-mono text-[13px] font-semibold text-text-primary">{node.id}</span>
        </div>
        <span
          className={`rounded border px-1.5 py-0.5 font-mono text-[9px] font-semibold tracking-widest ${badgeClass}`}
        >
          {statusLabel}
        </span>
      </div>

      {/* Meta rows */}
      <div className="flex flex-col gap-1.5">
        <MetaRow label="ADDR" value={node.address} />
        <MetaRow label="LAYERS" value={node.layers} highlight />
        <MetaRow label="LATENCY" value={isOnline ? `${node.latency}ms` : '—'} />
      </div>

      {/* Stats */}
      {isOnline && (
        <div className="mt-1 flex flex-col gap-1.5 rounded-md border border-border bg-bg-surface p-2.5">
          <div className="flex items-center gap-2">
            <span className="w-7 font-mono text-[9px] tracking-wide text-text-dim">GPU</span>
            <StatBar value={node.gpu} color="cyan" />
            <span className="w-8 text-right font-mono text-[10px] text-text-secondary">
              {node.gpu}%
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-7 font-mono text-[9px] tracking-wide text-text-dim">CPU</span>
            <StatBar value={node.cpu} color="green" />
            <span className="w-8 text-right font-mono text-[10px] text-text-secondary">
              {node.cpu}%
            </span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className="w-7 font-mono text-[9px] tracking-wide text-text-dim">VRAM</span>
            <span className="font-mono text-[10px] text-text-secondary">{node.vram}</span>
          </div>
        </div>
      )}

      {/* Action button */}
      <button
        onClick={() => (isOnline ? onDisconnect(node.id) : onConnect(node.id))}
        disabled={isConnecting}
        className={`
          mt-1 w-full rounded-md border py-1.5 font-mono text-[11px] font-semibold
          tracking-wide transition-all duration-150
          ${isConnecting ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}
          ${
            isOnline
              ? 'border-red/30 bg-red-dim text-red hover:bg-red/20'
              : 'border-cyan/30 bg-cyan-dim text-cyan hover:bg-cyan/20'
          }
        `}
      >
        {isConnecting ? 'Connecting...' : isOnline ? 'Disconnect' : 'Connect'}
      </button>
    </div>
  )
}

function MetaRow({
  label,
  value,
  highlight
}: {
  label: string
  value: string
  highlight?: boolean
}): React.JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <span className="w-12 flex-shrink-0 font-mono text-[9px] tracking-widest text-text-dim">
        {label}
      </span>
      <span className={`font-mono text-[11px] ${highlight ? 'text-cyan' : 'text-text-secondary'}`}>
        {value}
      </span>
    </div>
  )
}
