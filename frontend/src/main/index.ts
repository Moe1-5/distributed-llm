import { app, shell, BrowserWindow, ipcMain, dialog, net, protocol } from 'electron'
import type { IpcMainInvokeEvent, OpenDialogOptions, SaveDialogOptions } from 'electron'
import { execFile, spawn } from 'child_process'
import { existsSync, readFileSync } from 'fs'
import { mkdir, readFile, rename, writeFile } from 'fs/promises'
import { basename, join } from 'path'
import { pathToFileURL } from 'url'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import {
  DEFAULT_BACKEND_LAUNCHER_CONFIG,
  WslBackendLauncher,
  validateBackendLauncherConfig,
  type BackendLauncherConfig,
  type BackendLauncherRuntime,
  type LauncherChild
} from './backendLauncher'
import {
  readArtifactIdentity,
  resolvePackagedArtifactPath,
  type ArtifactIdentity
} from './artifactIdentity'
import {
  isTrustedExternalUrl,
  isTrustedRendererUrl,
  resolveRendererAssetPath
} from './securityPolicy'

declare const __DISTRIBLLM_SOURCE_COMMIT__: string
declare const __DISTRIBLLM_SOURCE_DIRTY__: boolean

const PACKAGED_RENDERER_URL = 'distribllm://app/index.html'

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'distribllm',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true
    }
  }
])

// Fix WSL GPU process errors — disable GPU rendering in WSL
// since WSL doesn't have proper GPU access for Chromium rendering
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('disable-gpu-compositing')
app.commandLine.appendSwitch('disable-software-rasterizer')
app.commandLine.appendSwitch('disable-dev-shm-usage')

function isWsl(): boolean {
  try {
    return readFileSync('/proc/version', 'utf8').toLowerCase().includes('microsoft')
  } catch {
    return false
  }
}

function openWithWindowsDefaultBrowser(url: string): Promise<boolean> {
  const cmdPath = '/mnt/c/Windows/system32/cmd.exe'
  if (!isWsl() || !existsSync(cmdPath)) return Promise.resolve(false)

  return new Promise((resolve) => {
    execFile(cmdPath, ['/c', 'start', '', url], (error) => {
      if (error) {
        console.warn(`Failed to open external URL through Windows default browser ${url}:`, error)
        resolve(false)
      } else {
        resolve(true)
      }
    })
  })
}

function runFile(file: string, args: string[]): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { windowsHide: true, encoding: 'utf8' }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr.trim() || error.message))
        return
      }
      resolve({ stdout, stderr })
    })
  })
}

function createBackendRuntime(): BackendLauncherRuntime {
  const testPlatform =
    !app.isPackaged && process.env['DISTRIBLLM_ELECTRON_TEST_PLATFORM'] === 'win32'
      ? 'win32'
      : process.platform
  return {
    platform: testPlatform,
    run: runFile,
    spawn: (file, args) =>
      spawn(file, args, {
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe']
      }) as LauncherChild,
    health: async (url) => {
      try {
        const response = await fetch(url, { signal: AbortSignal.timeout(5000) })
        return response.ok
      } catch {
        return false
      }
    },
    sleep: (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    now: () => new Date().toISOString()
  }
}

function backendConfigPath(): string {
  return join(app.getPath('userData'), 'backend-launcher.json')
}

async function loadBackendConfig(): Promise<BackendLauncherConfig> {
  try {
    const stored = JSON.parse(
      await readFile(backendConfigPath(), 'utf8')
    ) as Partial<BackendLauncherConfig>
    return {
      ...DEFAULT_BACKEND_LAUNCHER_CONFIG,
      ...stored,
      initialPeers: Array.isArray(stored.initialPeers) ? stored.initialPeers : [],
      trustedRelays: Array.isArray(stored.trustedRelays) ? stored.trustedRelays : []
    }
  } catch {
    return { ...DEFAULT_BACKEND_LAUNCHER_CONFIG }
  }
}

async function saveBackendConfig(config: BackendLauncherConfig): Promise<void> {
  const errors = validateBackendLauncherConfig(config)
  if (errors.length > 0) throw new Error(errors.join(' '))

  const target = backendConfigPath()
  const temporary = `${target}.tmp`
  await mkdir(app.getPath('userData'), { recursive: true })
  await writeFile(temporary, `${JSON.stringify(config, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600
  })
  await rename(temporary, target)
}

let backendLauncher: WslBackendLauncher | null = null
let appShutdownStarted = false

function packagedRendererUrl(): string {
  return PACKAGED_RENDERER_URL
}

function installPackagedRendererProtocol(): void {
  const rendererRoot = join(__dirname, '../renderer')
  protocol.handle('distribllm', (request) => {
    const target = resolveRendererAssetPath(rendererRoot, request.url)
    if (!target) return new Response('Not found', { status: 404 })
    return net.fetch(pathToFileURL(target).toString())
  })
}

function assertTrustedIpcSender(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url ?? ''
  const developmentUrl = is.dev ? process.env['ELECTRON_RENDERER_URL'] : undefined
  if (!isTrustedRendererUrl(senderUrl, packagedRendererUrl(), developmentUrl)) {
    throw new Error('IPC request rejected from an untrusted renderer.')
  }
}

function broadcastBackendStatus(): void {
  if (!backendLauncher) return
  const status = backendLauncher.getStatus()
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send('backend-launcher:status', status)
  }
}

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
    if (is.dev) mainWindow.webContents.openDevTools() // add this
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    if (isTrustedExternalUrl(details.url)) {
      shell.openExternal(details.url).catch((error) => {
        console.warn(`Failed to open external URL ${details.url}:`, error)
      })
    }
    return { action: 'deny' }
  })

  mainWindow.webContents.on('will-navigate', (event, url) => {
    const developmentUrl = is.dev ? process.env['ELECTRON_RENDERER_URL'] : undefined
    if (!isTrustedRendererUrl(url, packagedRendererUrl(), developmentUrl)) {
      event.preventDefault()
    }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadURL(PACKAGED_RENDERER_URL)
  }
}

app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.electron')
  if (!is.dev || !process.env['ELECTRON_RENDERER_URL']) {
    installPackagedRendererProtocol()
  }

  const backendConfig = await loadBackendConfig()
  const expectedBackendSourceCommit =
    app.isPackaged &&
    !__DISTRIBLLM_SOURCE_DIRTY__ &&
    /^[0-9a-f]{40}$/.test(__DISTRIBLLM_SOURCE_COMMIT__)
      ? __DISTRIBLLM_SOURCE_COMMIT__
      : null
  backendLauncher = new WslBackendLauncher(
    backendConfig,
    createBackendRuntime(),
    expectedBackendSourceCommit
  )
  backendLauncher.on('status', broadcastBackendStatus)

  ipcMain.handle('backend-launcher:get-status', (event) => {
    assertTrustedIpcSender(event)
    return backendLauncher?.getStatus()
  })
  ipcMain.handle('backend-launcher:get-config', (event) => {
    assertTrustedIpcSender(event)
    return backendLauncher?.getConfig()
  })
  ipcMain.handle('backend-launcher:save-config', async (event, config: BackendLauncherConfig) => {
    assertTrustedIpcSender(event)
    await saveBackendConfig(config)
    backendLauncher?.setConfig(config)
    return backendLauncher?.getConfig()
  })
  ipcMain.handle('backend-launcher:start', (event) => {
    assertTrustedIpcSender(event)
    return backendLauncher?.start()
  })
  ipcMain.handle('backend-launcher:stop', (event) => {
    assertTrustedIpcSender(event)
    return backendLauncher?.stop()
  })
  ipcMain.handle('backend-launcher:restart', (event) => {
    assertTrustedIpcSender(event)
    return backendLauncher?.restart()
  })
  ipcMain.handle('backend-launcher:export-acceptance-report', async (event) => {
    assertTrustedIpcSender(event)
    if (!backendLauncher) throw new Error('Managed backend launcher is unavailable.')

    const parentWindow = BrowserWindow.fromWebContents(event.sender)
    let artifact: ArtifactIdentity | null = null
    if (app.isPackaged) {
      try {
        artifact = await readArtifactIdentity(
          resolvePackagedArtifactPath(process.execPath, process.env['PORTABLE_EXECUTABLE_FILE'])
        )
      } catch (error) {
        console.warn('Could not identify the packaged executable for acceptance:', error)
      }
    }
    const report = backendLauncher.getAcceptanceReport({
      version: app.getVersion(),
      packaged: app.isPackaged,
      platform: process.platform,
      arch: process.arch,
      sourceCommit: __DISTRIBLLM_SOURCE_COMMIT__,
      sourceDirty: __DISTRIBLLM_SOURCE_DIRTY__,
      artifactFileName: artifact?.fileName ?? null,
      artifactSha256: artifact?.sha256 ?? null,
      artifactBytes: artifact?.bytes ?? null
    })
    const date = report.capturedAt.slice(0, 10)
    const options: SaveDialogOptions = {
      title: 'Export Windows acceptance report',
      defaultPath: `distribllm-windows-acceptance-${date}.json`,
      filters: [{ name: 'JSON report', extensions: ['json'] }]
    }
    const result = parentWindow
      ? await dialog.showSaveDialog(parentWindow, options)
      : await dialog.showSaveDialog(options)
    if (result.canceled || !result.filePath) {
      return { canceled: true, fileName: null, reportOk: null }
    }

    await writeFile(result.filePath, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
    return {
      canceled: false,
      fileName: basename(result.filePath),
      reportOk: report.ok
    }
  })

  ipcMain.handle('select-local-model-directory', async (event) => {
    assertTrustedIpcSender(event)
    const parentWindow = BrowserWindow.fromWebContents(event.sender)
    const options: OpenDialogOptions = {
      title: 'Select downloaded model folder',
      properties: ['openDirectory']
    }
    const result = parentWindow
      ? await dialog.showOpenDialog(parentWindow, options)
      : await dialog.showOpenDialog(options)
    if (result.canceled) return null
    return result.filePaths[0] ?? null
  })

  ipcMain.handle('open-external-url', async (event, url: string) => {
    assertTrustedIpcSender(event)
    if (!isTrustedExternalUrl(url)) {
      throw new Error('Only Hugging Face URLs can be opened from this action.')
    }
    try {
      await shell.openExternal(url)
      return true
    } catch (error) {
      console.warn(`Failed to open external URL ${url}:`, error)
      return openWithWindowsDefaultBrowser(url)
    }
  })

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  createWindow()

  if (backendLauncher.getStatus().managed && backendConfig.autoStart) {
    void backendLauncher.start()
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('before-quit', (event) => {
  if (appShutdownStarted || !backendLauncher) return
  event.preventDefault()
  appShutdownStarted = true
  void backendLauncher.stop().finally(() => app.quit())
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
