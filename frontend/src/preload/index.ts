import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import type {
  AcceptanceReportExportResult,
  BackendLauncherConfig,
  BackendLauncherStatus
} from '../main/backendLauncher'

// Custom APIs for renderer
const api = {
  selectLocalModelDirectory: (): Promise<string | null> =>
    ipcRenderer.invoke('select-local-model-directory') as Promise<string | null>,
  openExternalUrl: (url: string): Promise<boolean> =>
    ipcRenderer.invoke('open-external-url', url) as Promise<boolean>,
  getBackendLauncherStatus: (): Promise<BackendLauncherStatus> =>
    ipcRenderer.invoke('backend-launcher:get-status') as Promise<BackendLauncherStatus>,
  getBackendLauncherConfig: (): Promise<BackendLauncherConfig> =>
    ipcRenderer.invoke('backend-launcher:get-config') as Promise<BackendLauncherConfig>,
  saveBackendLauncherConfig: (config: BackendLauncherConfig): Promise<BackendLauncherConfig> =>
    ipcRenderer.invoke('backend-launcher:save-config', config) as Promise<BackendLauncherConfig>,
  startBackend: (): Promise<BackendLauncherStatus> =>
    ipcRenderer.invoke('backend-launcher:start') as Promise<BackendLauncherStatus>,
  stopBackend: (): Promise<BackendLauncherStatus> =>
    ipcRenderer.invoke('backend-launcher:stop') as Promise<BackendLauncherStatus>,
  restartBackend: (): Promise<BackendLauncherStatus> =>
    ipcRenderer.invoke('backend-launcher:restart') as Promise<BackendLauncherStatus>,
  exportWindowsAcceptanceReport: (): Promise<AcceptanceReportExportResult> =>
    ipcRenderer.invoke(
      'backend-launcher:export-acceptance-report'
    ) as Promise<AcceptanceReportExportResult>,
  onBackendLauncherStatus: (callback: (status: BackendLauncherStatus) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: BackendLauncherStatus): void => {
      callback(status)
    }
    ipcRenderer.on('backend-launcher:status', listener)
    return () => ipcRenderer.removeListener('backend-launcher:status', listener)
  }
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}
