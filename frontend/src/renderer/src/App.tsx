import React, { useEffect, useState } from 'react'
import Chat from './pages/Chat'
import Dashboard from './pages/Dashboard'
import Incentives from './pages/Incentives'
import Monitoring from './pages/Monitoring'
import Network from './pages/Network'
import Settings from './pages/Settings'
import Sidebar from './components/Sidebar'
import './assets/main.css'

export type Page = 'dashboard' | 'network' | 'chat' | 'monitoring' | 'incentives' | 'settings'

interface PageErrorBoundaryProps {
  children: React.ReactNode
}

interface PageErrorBoundaryState {
  error: Error | null
}

class PageErrorBoundary extends React.Component<PageErrorBoundaryProps, PageErrorBoundaryState> {
  state: PageErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): PageErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error('Page render failed', error, info)
  }

  render(): React.ReactNode {
    if (this.state.error) {
      return (
        <div className="m-7 rounded-xl border border-red/20 bg-red/5 p-5">
          <h1 className="text-lg font-semibold text-red">This page could not be rendered</h1>
          <p className="mt-2 text-sm text-text-secondary">
            Restart the backend and Electron if they are running different versions, or navigate
            away and return to retry.
          </p>
          <p className="mt-3 font-mono text-[11px] text-red/80">{this.state.error.message}</p>
        </div>
      )
    }

    return this.props.children
  }
}

function App(): React.JSX.Element {
  const [currentPage, setCurrentPage] = useState<Page>('dashboard')
  const [backendLauncherStatus, setBackendLauncherStatus] = useState<Awaited<
    ReturnType<Window['api']['getBackendLauncherStatus']>
  > | null>(null)

  useEffect(() => {
    void window.api.getBackendLauncherStatus().then(setBackendLauncherStatus)
    return window.api.onBackendLauncherStatus(setBackendLauncherStatus)
  }, [])

  const showLauncherBanner =
    backendLauncherStatus?.managed && backendLauncherStatus.state !== 'ready'

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg-base">
      <Sidebar currentPage={currentPage} onNavigate={setCurrentPage} />
      <main className="flex flex-1 flex-col overflow-hidden">
        {showLauncherBanner && (
          <button
            type="button"
            onClick={() => setCurrentPage('settings')}
            className="flex min-h-10 flex-shrink-0 items-center justify-between gap-4 border-b border-amber/30 bg-amber/10 px-5 py-2 text-left"
          >
            <span className="min-w-0 truncate font-mono text-[11px] text-amber">
              {backendLauncherStatus.message}
            </span>
            <span className="flex-shrink-0 font-mono text-[10px] font-semibold text-amber">
              SETTINGS
            </span>
          </button>
        )}
        <PageErrorBoundary key={currentPage}>
          {currentPage === 'dashboard' && <Dashboard />}
          {currentPage === 'chat' && <Chat />}
          {currentPage === 'network' && <Network />}
          {currentPage === 'monitoring' && <Monitoring />}
          {currentPage === 'incentives' && <Incentives />}
          {currentPage === 'settings' && <Settings />}
        </PageErrorBoundary>
      </main>
    </div>
  )
}

export default App
