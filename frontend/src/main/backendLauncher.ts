import { EventEmitter } from 'events'

export type BackendLauncherState =
  | 'idle'
  | 'needs_setup'
  | 'checking'
  | 'missing_wsl'
  | 'missing_distro'
  | 'installing_backend'
  | 'starting_backend'
  | 'ready'
  | 'stopping'
  | 'failed'

export type NetworkMode = 'auto' | 'direct' | 'relay'

export interface BackendLauncherConfig {
  autoStart: boolean
  distroName: string
  backendPath: string
  backendUrl: string
  syncDependencies: boolean
  networkMode: NetworkMode
  initialPeers: string[]
  trustedRelays: string[]
  relayWaitTimeoutSeconds: number
}

export interface BackendLauncherStatus {
  state: BackendLauncherState
  message: string
  diagnosticCode: string | null
  detail: string | null
  managed: boolean
  updatedAt: string
}

export interface BackendLauncherTransition {
  state: BackendLauncherState
  diagnosticCode: string | null
  updatedAt: string
}

export interface BackendLauncherEvidence {
  transitions: BackendLauncherTransition[]
  wslAvailable: boolean
  distroPresent: boolean
  distroWsl2: boolean
  dependencySyncRequested: boolean
  dependencySyncCompleted: boolean
  backendHealthReady: boolean
  backendStoppedCleanly: boolean
  backendSourceCommit: string | null
  backendSourceClean: boolean
}

export interface WindowsAcceptanceApplication {
  version: string
  packaged: boolean
  platform: NodeJS.Platform
  arch: string
  sourceCommit: string | null
  sourceDirty: boolean
  artifactFileName: string | null
  artifactSha256: string | null
  artifactBytes: number | null
}

export interface WindowsAcceptanceReport {
  schemaVersion: 3
  capturedAt: string
  application: WindowsAcceptanceApplication
  backendRuntime: {
    sourceCommit: string | null
    sourceClean: boolean
  }
  configuration: {
    distroName: string
    backendPathConfigured: boolean
    backendUrl: string
    syncDependencies: boolean
    networkMode: NetworkMode
    initialPeerCount: number
    trustedRelayCount: number
    relayWaitTimeoutSeconds: number
  }
  launcher: {
    currentStatus: BackendLauncherTransition
    transitions: BackendLauncherTransition[]
  }
  checks: {
    windowsHost: boolean
    packagedApplication: boolean
    sourceCommitIdentified: boolean
    backendSourceMatchesApplication: boolean
    artifactIdentified: boolean
    wslAvailable: boolean
    distroPresent: boolean
    distroWsl2: boolean
    dependencySyncCompleted: boolean
    backendHealthReady: boolean
    backendStoppedCleanly: boolean
  }
  ok: boolean
}

export interface AcceptanceReportExportResult {
  canceled: boolean
  fileName: string | null
  reportOk: boolean | null
}

export interface CommandResult {
  stdout: string
  stderr: string
}

export interface LauncherChild extends EventEmitter {
  exitCode: number | null
  kill(signal?: NodeJS.Signals): boolean
  stderr?: NodeJS.ReadableStream | null
}

export interface BackendLauncherRuntime {
  platform: NodeJS.Platform
  run(file: string, args: string[]): Promise<CommandResult>
  spawn(file: string, args: string[]): LauncherChild
  health(url: string): Promise<boolean>
  sleep(milliseconds: number): Promise<void>
  now(): string
}

export const PROJECT_VPS_RELAY_MADDR =
  '/ip4/178.156.212.0/tcp/7001/p2p/QmTXjKiMggt92DP4CLbDwMCfLd4L1aNKyBnja5apT2ZZL2'

export const DEFAULT_BACKEND_LAUNCHER_CONFIG: BackendLauncherConfig = {
  autoStart: true,
  distroName: 'Ubuntu',
  backendPath: '',
  backendUrl: 'http://127.0.0.1:8000',
  syncDependencies: true,
  networkMode: 'auto',
  initialPeers: [PROJECT_VPS_RELAY_MADDR],
  trustedRelays: [PROJECT_VPS_RELAY_MADDR],
  relayWaitTimeoutSeconds: 90
}

const DISTRO_PATTERN = /^[A-Za-z0-9._-]+$/
const HEALTH_ATTEMPTS = 80
const HEALTH_INTERVAL_MS = 750
const MAX_ACCEPTANCE_TRANSITIONS = 100

function acceptanceTransition(status: BackendLauncherStatus): BackendLauncherTransition {
  return {
    state: status.state,
    diagnosticCode: status.diagnosticCode,
    updatedAt: status.updatedAt
  }
}

export function buildWindowsAcceptanceReport(
  config: BackendLauncherConfig,
  status: BackendLauncherStatus,
  evidence: BackendLauncherEvidence,
  application: WindowsAcceptanceApplication,
  capturedAt: string
): WindowsAcceptanceReport {
  const checks = {
    windowsHost: application.platform === 'win32',
    packagedApplication: application.packaged,
    sourceCommitIdentified:
      /^[0-9a-f]{40}$/.test(application.sourceCommit ?? '') && !application.sourceDirty,
    backendSourceMatchesApplication:
      /^[0-9a-f]{40}$/.test(evidence.backendSourceCommit ?? '') &&
      evidence.backendSourceClean &&
      evidence.backendSourceCommit === application.sourceCommit,
    artifactIdentified:
      /^[0-9a-f]{64}$/.test(application.artifactSha256 ?? '') &&
      Number.isInteger(application.artifactBytes) &&
      (application.artifactBytes ?? 0) > 0 &&
      Boolean(application.artifactFileName?.trim()),
    wslAvailable: evidence.wslAvailable,
    distroPresent: evidence.distroPresent,
    distroWsl2: evidence.distroWsl2,
    dependencySyncCompleted:
      config.syncDependencies &&
      evidence.dependencySyncRequested &&
      evidence.dependencySyncCompleted,
    backendHealthReady: evidence.backendHealthReady,
    backendStoppedCleanly: evidence.backendStoppedCleanly && status.state === 'idle'
  }

  return {
    schemaVersion: 3,
    capturedAt,
    application: { ...application },
    backendRuntime: {
      sourceCommit: evidence.backendSourceCommit,
      sourceClean: evidence.backendSourceClean
    },
    configuration: {
      distroName: config.distroName,
      backendPathConfigured: Boolean(config.backendPath.trim()),
      backendUrl: config.backendUrl,
      syncDependencies: config.syncDependencies,
      networkMode: config.networkMode,
      initialPeerCount: config.initialPeers.length,
      trustedRelayCount: config.trustedRelays.length,
      relayWaitTimeoutSeconds: config.relayWaitTimeoutSeconds
    },
    launcher: {
      currentStatus: acceptanceTransition(status),
      transitions: evidence.transitions.map((transition) => ({ ...transition }))
    },
    checks,
    ok: Object.values(checks).every(Boolean)
  }
}

export function decodeWslOutput(output: string): string {
  return output
    .replace(/^\uFEFF/, '')
    .replaceAll('\u0000', '')
    .replaceAll('\r', '')
}

export function parseWslDistros(output: string): string[] {
  return decodeWslOutput(output)
    .split('\n')
    .map((line) => line.replace(/^\*\s*/, '').trim())
    .filter(Boolean)
}

export interface WslDistroInfo {
  name: string
  version: number | null
}

export function parseWslDistroInfo(output: string): WslDistroInfo[] {
  return decodeWslOutput(output)
    .split('\n')
    .map((line) => line.replace(/^\*\s*/, '').trim())
    .filter(Boolean)
    .filter((line) => !/^name\s+/i.test(line))
    .map((line) => {
      const fields = line.split(/\s{2,}/)
      const rawVersion = fields.at(-1) ?? ''
      return {
        name: fields[0],
        version: /^\d+$/.test(rawVersion) ? Number(rawVersion) : null
      }
    })
}

export function shellQuote(value: string): string {
  if (value.includes('\u0000') || value.includes('\n') || value.includes('\r')) {
    throw new Error('Launcher values must not contain control characters.')
  }
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

export function buildWslBashArgs(distroName: string, script: string): string[] {
  const encodedScript = Buffer.from(script, 'utf8').toString('base64')
  const decodeCommand = `printf '%s' ${shellQuote(encodedScript)} | base64 --decode | bash`
  return ['--distribution', distroName, '--', 'bash', '-lc', decodeCommand]
}

export function validateBackendLauncherConfig(config: BackendLauncherConfig): string[] {
  const errors: string[] = []
  if (typeof config.distroName !== 'string' || !DISTRO_PATTERN.test(config.distroName)) {
    errors.push('WSL distro name may contain only letters, numbers, dots, underscores, and dashes.')
  }
  if (typeof config.backendPath !== 'string' || !config.backendPath.trim()) {
    errors.push('Backend path is required.')
  } else if (!config.backendPath.startsWith('/')) {
    errors.push('Backend path must be an absolute WSL path.')
  }

  if (typeof config.backendUrl !== 'string') {
    errors.push('Backend URL is invalid.')
  } else {
    try {
      const backendUrl = new URL(config.backendUrl)
      if (
        backendUrl.protocol !== 'http:' ||
        !['127.0.0.1', 'localhost', '[::1]'].includes(backendUrl.hostname) ||
        backendUrl.username ||
        backendUrl.password ||
        backendUrl.search ||
        backendUrl.hash
      ) {
        errors.push(
          'Backend URL must use HTTP on a loopback address without credentials or query parameters.'
        )
      }
    } catch {
      errors.push('Backend URL is invalid.')
    }
  }

  if (!['auto', 'direct', 'relay'].includes(config.networkMode)) {
    errors.push('Network mode must be auto, direct, or relay.')
  }
  if (
    !Number.isInteger(config.relayWaitTimeoutSeconds) ||
    config.relayWaitTimeoutSeconds < 0 ||
    config.relayWaitTimeoutSeconds > 3600
  ) {
    errors.push('Relay wait timeout must be an integer between 0 and 3600 seconds.')
  }
  if (!Array.isArray(config.initialPeers) || !Array.isArray(config.trustedRelays)) {
    errors.push('Bootstrap peers and trusted relays must be address lists.')
    return errors
  }
  for (const address of [...config.initialPeers, ...config.trustedRelays]) {
    if (
      typeof address !== 'string' ||
      !address.startsWith('/') ||
      address.includes('\u0000') ||
      address.includes('\r') ||
      address.includes('\n')
    ) {
      errors.push(`Invalid multiaddress: ${String(address)}`)
    }
  }
  if (config.networkMode !== 'direct' && config.initialPeers.length === 0) {
    errors.push('Auto and relay modes require at least one bootstrap peer address.')
  }
  if (config.networkMode !== 'direct' && config.trustedRelays.length === 0) {
    errors.push('Auto and relay modes require at least one trusted relay address.')
  }
  return errors
}

function launcherEnvironment(config: BackendLauncherConfig): Record<string, string> {
  return {
    DISTRIBLLM_NETWORK_MODE: config.networkMode,
    DISTRIBLLM_INITIAL_PEERS: config.initialPeers.join(','),
    DISTRIBLLM_TRUSTED_RELAYS: config.trustedRelays.join(','),
    DISTRIBLLM_AUTO_RELAY: config.networkMode === 'direct' ? 'false' : 'true',
    DISTRIBLLM_RELAY_WAIT_TIMEOUT: String(config.relayWaitTimeoutSeconds)
  }
}

const WSL_RUNTIME_DIR_SCRIPT = [
  ': "${HOME:?HOME is required}"',
  'export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"',
  'export XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"',
  'export UV_CACHE_DIR="${UV_CACHE_DIR:-$XDG_CACHE_HOME/uv}"',
  'mkdir -p "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$UV_CACHE_DIR"'
]

export function buildBackendLaunchScript(config: BackendLauncherConfig): string {
  const environment = Object.entries(launcherEnvironment(config))
    .map(([key, value]) => `${key}=${shellQuote(value)}`)
    .join(' ')
  const backendPath = shellQuote(config.backendPath)

  return [
    'set -eu',
    ...WSL_RUNTIME_DIR_SCRIPT,
    'state_dir="$XDG_STATE_HOME/distribllm"',
    'mkdir -p "$state_dir"',
    'pid_file="$state_dir/backend.pid"',
    `cd -- ${backendPath}`,
    `env ${environment} uv run --python 3.12 python main.py &`,
    'backend_pid=$!',
    'printf "%s\\n" "$backend_pid" > "$pid_file"',
    'cleanup() { rm -f "$pid_file"; }',
    'terminate() { kill -TERM "$backend_pid" 2>/dev/null || true; wait "$backend_pid" || true; cleanup; exit 0; }',
    'trap terminate TERM INT',
    'trap cleanup EXIT',
    'wait "$backend_pid"'
  ].join('\n')
}

export function buildDependencySyncScript(config: BackendLauncherConfig): string {
  return [
    'set -eu',
    ...WSL_RUNTIME_DIR_SCRIPT,
    'command -v uv >/dev/null 2>&1 || { echo "uv is not installed" >&2; exit 127; }',
    `cd -- ${shellQuote(config.backendPath)}`,
    'test -f pyproject.toml || { echo "pyproject.toml is missing" >&2; exit 2; }',
    'uv sync --python 3.12'
  ].join('\n')
}

export function buildBackendSourceIdentityScript(config: BackendLauncherConfig): string {
  return [
    'set -eu',
    `cd -- ${shellQuote(config.backendPath)}`,
    'command -v git >/dev/null 2>&1 || { echo "git is not installed" >&2; exit 127; }',
    'git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "backend path is not a Git checkout" >&2; exit 2; }',
    'git rev-parse HEAD',
    'test -z "$(git status --porcelain --untracked-files=no)" && printf "clean\\n" || printf "dirty\\n"'
  ].join('\n')
}

export function buildBackendStopScript(): string {
  return [
    'set -eu',
    ...WSL_RUNTIME_DIR_SCRIPT,
    'pid_file="$XDG_STATE_HOME/distribllm/backend.pid"',
    'test -f "$pid_file" || exit 0',
    'backend_pid="$(cat "$pid_file")"',
    'case "$backend_pid" in (*[!0-9]*|"") rm -f "$pid_file"; exit 1;; esac',
    'kill -TERM "$backend_pid" 2>/dev/null || true',
    'for _ in 1 2 3 4 5 6 7 8 9 10; do',
    '  kill -0 "$backend_pid" 2>/dev/null || { rm -f "$pid_file"; exit 0; }',
    '  sleep 0.5',
    'done',
    'kill -KILL "$backend_pid" 2>/dev/null || true',
    'rm -f "$pid_file"'
  ].join('\n')
}

function diagnosticFromOutput(output: string): { code: string; message: string } {
  const normalized = output.toLowerCase()
  if (normalized.includes('address already in use')) {
    return { code: 'backend_port_conflict', message: 'Backend port is already in use.' }
  }
  if (normalized.includes('uv is not installed') || normalized.includes('uv: command not found')) {
    return { code: 'uv_missing', message: 'uv is not installed in the selected WSL distro.' }
  }
  if (
    normalized.includes('mkdir: cannot create directory') &&
    normalized.includes('no such file or directory')
  ) {
    return {
      code: 'runtime_directory_invalid',
      message: 'The WSL runtime directory configuration is invalid.'
    }
  }
  if (normalized.includes('pyproject.toml is missing') || normalized.includes('no such file')) {
    return {
      code: 'backend_path_invalid',
      message: 'Backend files were not found at the configured path.'
    }
  }
  return { code: 'backend_start_failed', message: 'The managed backend failed to start.' }
}

export class WslBackendLauncher extends EventEmitter {
  private config: BackendLauncherConfig
  private child: LauncherChild | null = null
  private stderr = ''
  private startPromise: Promise<BackendLauncherStatus> | null = null
  private status: BackendLauncherStatus
  private evidence: BackendLauncherEvidence

  constructor(
    config: BackendLauncherConfig,
    private readonly runtime: BackendLauncherRuntime,
    private readonly expectedSourceCommit: string | null = null
  ) {
    super()
    this.config = { ...config }
    this.status = {
      state: 'idle',
      message: 'Managed backend is stopped.',
      diagnosticCode: null,
      detail: null,
      managed: runtime.platform === 'win32',
      updatedAt: runtime.now()
    }
    this.evidence = this.initialEvidence()
  }

  private initialEvidence(): BackendLauncherEvidence {
    return {
      transitions: [acceptanceTransition(this.status)],
      wslAvailable: false,
      distroPresent: false,
      distroWsl2: false,
      dependencySyncRequested: false,
      dependencySyncCompleted: false,
      backendHealthReady: false,
      backendStoppedCleanly: false,
      backendSourceCommit: null,
      backendSourceClean: false
    }
  }

  getConfig(): BackendLauncherConfig {
    return {
      ...this.config,
      initialPeers: [...this.config.initialPeers],
      trustedRelays: [...this.config.trustedRelays]
    }
  }

  setConfig(config: BackendLauncherConfig): void {
    this.config = {
      ...config,
      initialPeers: [...config.initialPeers],
      trustedRelays: [...config.trustedRelays]
    }
    this.evidence = this.initialEvidence()
  }

  getStatus(): BackendLauncherStatus {
    return { ...this.status }
  }

  getAcceptanceReport(application: WindowsAcceptanceApplication): WindowsAcceptanceReport {
    return buildWindowsAcceptanceReport(
      this.config,
      this.status,
      this.evidence,
      application,
      this.runtime.now()
    )
  }

  private update(
    state: BackendLauncherState,
    message: string,
    diagnosticCode: string | null = null,
    detail: string | null = null
  ): BackendLauncherStatus {
    this.status = {
      state,
      message,
      diagnosticCode,
      detail,
      managed: this.runtime.platform === 'win32',
      updatedAt: this.runtime.now()
    }
    this.evidence.transitions = [
      ...this.evidence.transitions,
      acceptanceTransition(this.status)
    ].slice(-MAX_ACCEPTANCE_TRANSITIONS)
    this.emit('status', this.getStatus())
    return this.getStatus()
  }

  start(): Promise<BackendLauncherStatus> {
    if (this.startPromise) return this.startPromise
    this.startPromise = this.startInternal().finally(() => {
      this.startPromise = null
    })
    return this.startPromise
  }

  private async startInternal(): Promise<BackendLauncherStatus> {
    if (this.runtime.platform !== 'win32') {
      return this.update(
        'failed',
        'Managed WSL launch is available only in the Windows desktop package.',
        'unsupported_platform'
      )
    }
    if (this.status.state === 'ready') return this.getStatus()

    const validationErrors = validateBackendLauncherConfig(this.config)
    if (validationErrors.length > 0) {
      return this.update(
        'needs_setup',
        'Managed backend setup is incomplete.',
        'configuration_invalid',
        validationErrors.join(' ')
      )
    }

    this.update('checking', 'Checking WSL 2 and the configured distro.')
    try {
      await this.runtime.run('wsl.exe', ['--status'])
      this.evidence.wslAvailable = true
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      return this.update('missing_wsl', 'WSL 2 is unavailable.', 'wsl_missing', detail)
    }

    let distros: WslDistroInfo[]
    try {
      const result = await this.runtime.run('wsl.exe', ['--list', '--verbose'])
      distros = parseWslDistroInfo(result.stdout)
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      return this.update('failed', 'Could not list WSL distros.', 'wsl_list_failed', detail)
    }
    const selectedDistro = distros.find(
      (distro) => distro.name.toLowerCase() === this.config.distroName.toLowerCase()
    )
    if (!selectedDistro) {
      return this.update(
        'missing_distro',
        `WSL distro ${this.config.distroName} is not installed.`,
        'distro_missing',
        distros.length > 0
          ? `Installed: ${distros.map((distro) => distro.name).join(', ')}`
          : 'No WSL distros were found.'
      )
    }
    this.evidence.distroPresent = true
    if (selectedDistro.version !== 2) {
      return this.update(
        'failed',
        `WSL distro ${this.config.distroName} must use WSL 2.`,
        'distro_not_wsl2',
        selectedDistro.version === null
          ? 'The distro version could not be determined.'
          : `Current version: ${selectedDistro.version}`
      )
    }
    this.evidence.distroWsl2 = true

    if (this.expectedSourceCommit) {
      try {
        const identity = await this.runtime.run(
          'wsl.exe',
          buildWslBashArgs(this.config.distroName, buildBackendSourceIdentityScript(this.config))
        )
        const [sourceCommit = '', cleanliness = ''] = decodeWslOutput(identity.stdout)
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
        this.evidence.backendSourceCommit = /^[0-9a-f]{40}$/.test(sourceCommit)
          ? sourceCommit
          : null
        this.evidence.backendSourceClean = cleanliness === 'clean'
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error)
        return this.update(
          'failed',
          'Could not identify the WSL backend source revision.',
          'backend_source_unidentified',
          detail
        )
      }
      if (
        this.evidence.backendSourceCommit !== this.expectedSourceCommit ||
        !this.evidence.backendSourceClean
      ) {
        return this.update(
          'failed',
          'WSL backend source does not match this application build.',
          'backend_source_mismatch',
          `Expected ${this.expectedSourceCommit}; found ${this.evidence.backendSourceCommit ?? 'unknown'} (${this.evidence.backendSourceClean ? 'clean' : 'dirty'} tracked source). Pull the packaged source commit before starting.`
        )
      }
    }

    if (this.config.syncDependencies) {
      this.evidence.dependencySyncRequested = true
      this.update('installing_backend', 'Synchronizing backend dependencies.')
      try {
        await this.runtime.run(
          'wsl.exe',
          buildWslBashArgs(this.config.distroName, buildDependencySyncScript(this.config))
        )
        this.evidence.dependencySyncCompleted = true
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error)
        const diagnostic = diagnosticFromOutput(detail)
        return this.update('failed', diagnostic.message, diagnostic.code, detail)
      }
    }

    if (await this.runtime.health(`${this.config.backendUrl.replace(/\/$/, '')}/status`)) {
      return this.update(
        'failed',
        'Backend port is already in use.',
        'backend_port_conflict',
        `${this.config.backendUrl} already responds before managed startup.`
      )
    }

    this.stderr = ''
    this.update('starting_backend', 'Starting the managed backend.')
    this.child = this.runtime.spawn(
      'wsl.exe',
      buildWslBashArgs(this.config.distroName, buildBackendLaunchScript(this.config))
    )
    this.child.stderr?.on('data', (chunk) => {
      this.stderr = `${this.stderr}${String(chunk)}`.slice(-8000)
    })
    this.child.once('exit', (code) => {
      this.child = null
      if (this.status.state === 'stopping' || this.status.state === 'idle') return
      const diagnostic = diagnosticFromOutput(this.stderr)
      this.update(
        'failed',
        diagnostic.message,
        diagnostic.code,
        `Process exited with code ${code ?? 'unknown'}. ${this.stderr}`.trim()
      )
    })
    this.child.once('error', (error) => {
      this.child = null
      this.update('failed', 'Could not launch wsl.exe.', 'wsl_spawn_failed', error.message)
    })

    for (let attempt = 0; attempt < HEALTH_ATTEMPTS; attempt += 1) {
      if (!this.child || this.child.exitCode !== null) return this.getStatus()
      if (await this.runtime.health(`${this.config.backendUrl.replace(/\/$/, '')}/status`)) {
        this.evidence.backendHealthReady = true
        return this.update('ready', 'Managed backend is ready.')
      }
      await this.runtime.sleep(HEALTH_INTERVAL_MS)
    }

    await this.stop()
    return this.update(
      'failed',
      'Backend health check timed out.',
      'backend_health_timeout',
      this.stderr || null
    )
  }

  async stop(): Promise<BackendLauncherStatus> {
    if (this.runtime.platform !== 'win32') {
      return this.update('idle', 'Managed backend is stopped.')
    }
    if (!this.child && this.status.state === 'idle') return this.getStatus()

    this.update('stopping', 'Stopping the managed backend.')
    try {
      await this.runtime.run(
        'wsl.exe',
        buildWslBashArgs(this.config.distroName, buildBackendStopScript())
      )
    } catch (error) {
      this.child?.kill('SIGTERM')
      const detail = error instanceof Error ? error.message : String(error)
      this.child = null
      return this.update(
        'failed',
        'Managed backend did not stop cleanly.',
        'backend_stop_failed',
        detail
      )
    }
    this.child?.kill('SIGTERM')
    this.child = null
    this.evidence.backendStoppedCleanly = true
    return this.update('idle', 'Managed backend is stopped.')
  }

  async restart(): Promise<BackendLauncherStatus> {
    const stopped = await this.stop()
    if (stopped.state === 'failed') return stopped
    return this.start()
  }
}
