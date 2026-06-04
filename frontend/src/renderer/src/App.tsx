import React, { useState } from 'react'
import Chat from './pages/Chat'
import Dashboard from './pages/Dashboard'
import Network from './pages/Network'
import Settings from './pages/Settings'
import Sidebar from './components/Sidebar'
import './assets/main.css'

export type Page = 'dashboard' | 'chat' | 'network' | 'settings'

function App(): React.JSX.Element {
  const [currentPage, setCurrentPage] = useState<Page>('dashboard')

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg-base">
      <Sidebar currentPage={currentPage} onNavigate={setCurrentPage} />
      <main className="flex flex-1 flex-col overflow-hidden">
        {currentPage === 'dashboard' && <Dashboard />}
        {currentPage === 'chat' && <Chat />}
        {currentPage === 'network' && <Network />}
        {currentPage === 'settings' && <Settings />}
      </main>
    </div>
  )
}

export default App
