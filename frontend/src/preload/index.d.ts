import { ElectronAPI } from '@electron-toolkit/preload'

export interface DistribLLMAPI {
  selectLocalModelDirectory: () => Promise<string | null>
  openExternalUrl: (url: string) => Promise<boolean>
}

declare global {
  interface Window {
    electron: ElectronAPI
    api: DistribLLMAPI
  }
}
