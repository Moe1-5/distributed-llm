import React from 'react'
import { Page } from '../App'

interface SidebarProps {
  currentPage: Page
  onNavigate: (page: Page) => void
}

const NAV_ITEMS: { id: Page; label: string; icon: string }[] = [
  { id: 'dashboard', label: 'Nodes', icon: '⬡' },
  { id: 'network', label: 'Network', icon: '◎' },
  { id: 'chat', label: 'Inference', icon: '◈' },
  { id: 'monitoring', label: 'Monitoring', icon: '▣' },
  { id: 'incentives', label: 'Incentives', icon: '◇' }
]

export default function Sidebar({ currentPage, onNavigate }: SidebarProps): React.JSX.Element {
  return (
    <aside className="flex w-[68px] min-w-[68px] flex-col border-r border-border bg-bg-surface select-none lg:w-[220px] lg:min-w-[220px]">
      {/* Logo */}
      <div className="flex items-center gap-3 border-b border-border px-4 py-5">
        <span className="text-2xl leading-none text-cyan">◈</span>
        <div className="hidden lg:block">
          <div className="font-mono text-[13px] font-semibold tracking-widest text-text-primary">
            DISTRIBLLM
          </div>
          <div className="mt-0.5 text-[10px] tracking-wide text-text-secondary">P2P Inference</div>
        </div>
      </div>

      {/* Main nav */}
      <nav className="flex flex-1 flex-col gap-0.5 p-2">
        {NAV_ITEMS.map((item) => {
          const active = currentPage === item.id
          return (
            <button
              type="button"
              key={item.id}
              onClick={() => onNavigate(item.id)}
              aria-label={`Open ${item.label}`}
              aria-current={active ? 'page' : undefined}
              className={`
                relative flex w-full items-center gap-2.5 rounded-md px-3 py-2.5
                text-left text-[13px] font-medium transition-all duration-150
                ${
                  active
                    ? 'border border-cyan/20 bg-cyan-dim text-cyan'
                    : 'border border-transparent text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                }
              `}
            >
              <span className={`text-base ${active ? 'text-cyan' : 'text-text-dim'}`}>
                {item.icon}
              </span>
              <span className="hidden lg:inline">{item.label}</span>
              {active && (
                <span className="ml-auto h-1.5 w-1.5 rounded-full bg-cyan shadow-[0_0_6px_#00d4ff]" />
              )}
            </button>
          )
        })}
      </nav>

      {/* Settings — pinned above footer */}
      <div className="px-2 pb-2">
        <button
          type="button"
          onClick={() => onNavigate('settings')}
          aria-label="Open Settings"
          aria-current={currentPage === 'settings' ? 'page' : undefined}
          className={`
            relative flex w-full items-center gap-2.5 rounded-md px-3 py-2.5
            text-left text-[13px] font-medium transition-all duration-150
            ${
              currentPage === 'settings'
                ? 'border border-cyan/20 bg-cyan-dim text-cyan'
                : 'border border-transparent text-text-secondary hover:bg-bg-hover hover:text-text-primary'
            }
          `}
        >
          <span
            className={`text-base ${currentPage === 'settings' ? 'text-cyan' : 'text-text-dim'}`}
          >
            ⚙
          </span>
          <span className="hidden lg:inline">Settings</span>
          {currentPage === 'settings' && (
            <span className="ml-auto h-1.5 w-1.5 rounded-full bg-cyan shadow-[0_0_6px_#00d4ff]" />
          )}
        </button>
      </div>

      {/* Footer */}
      <div
        className="border-t border-border px-4 py-3.5"
        title="Open Monitoring for authoritative DHT state"
      >
        <div className="flex items-center gap-2">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-text-dim" />
          <span className="hidden font-mono text-[11px] text-text-secondary lg:inline">
            Network status in Monitoring
          </span>
        </div>
        <div className="mt-1 hidden font-mono text-[10px] text-text-dim lg:block">v0.1.0-alpha</div>
      </div>
    </aside>
  )
}
