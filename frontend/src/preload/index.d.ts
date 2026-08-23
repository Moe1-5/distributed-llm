import type {
  AcceptanceReportExportResult,
  BackendLauncherConfig,
  BackendLauncherStatus
} from '../main/backendLauncher'

export interface DistribLLMAPI {
  selectLocalModelDirectory: () => Promise<string | null>
  openExternalUrl: (url: string) => Promise<boolean>
  getBackendLauncherStatus: () => Promise<BackendLauncherStatus>
  getBackendLauncherConfig: () => Promise<BackendLauncherConfig>
  saveBackendLauncherConfig: (config: BackendLauncherConfig) => Promise<BackendLauncherConfig>
  startBackend: () => Promise<BackendLauncherStatus>
  stopBackend: () => Promise<BackendLauncherStatus>
  restartBackend: () => Promise<BackendLauncherStatus>
  exportWindowsAcceptanceReport: () => Promise<AcceptanceReportExportResult>
  onBackendLauncherStatus: (callback: (status: BackendLauncherStatus) => void) => () => void
}

declare global {
  interface Window {
    api: DistribLLMAPI
  }
}
