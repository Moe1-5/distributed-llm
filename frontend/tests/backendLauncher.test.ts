import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import {
  DEFAULT_BACKEND_LAUNCHER_CONFIG,
  PROJECT_VPS_RELAY_MADDR,
  WslBackendLauncher,
  buildWindowsAcceptanceReport,
  buildBackendLaunchScript,
  buildPackagedBackendInstallScript,
  buildBackendSourceIdentityScript,
  buildDependencySyncScript,
  buildBackendStopScript,
  buildWindowsPathConversionScript,
  buildWslBashArgs,
  parseWslDistroInfo,
  parseWslDistros,
  validateBackendLauncherConfig,
  type BackendLauncherConfig,
  type BackendLauncherRuntime,
  type CommandResult,
  type LauncherChild,
  type WindowsAcceptanceApplication
} from '../src/main/backendLauncher'
import { readArtifactIdentity, resolvePackagedArtifactPath } from '../src/main/artifactIdentity'
import {
  isTrustedExternalUrl,
  isTrustedRendererUrl,
  resolveRendererAssetPath
} from '../src/main/securityPolicy'

const TEST_COMMIT = 'a'.repeat(40)
const TEST_ARTIFACT_SHA256 = 'b'.repeat(64)
const TEST_MANIFEST_SHA256 = 'd'.repeat(64)

function packagedApplication(
  overrides: Partial<WindowsAcceptanceApplication> = {}
): WindowsAcceptanceApplication {
  return {
    version: '1.0.0',
    packaged: true,
    platform: 'win32' as const,
    arch: 'x64',
    sourceCommit: TEST_COMMIT,
    sourceDirty: false,
    artifactFileName: 'DistribLLM-1.0.0-portable.exe',
    artifactSha256: TEST_ARTIFACT_SHA256,
    artifactBytes: 1024,
    ...overrides
  }
}

class FakeChild extends EventEmitter implements LauncherChild {
  exitCode: number | null = null
  stderr = new PassThrough()
  killedWith: NodeJS.Signals | null = null

  kill(signal: NodeJS.Signals = 'SIGTERM'): boolean {
    this.killedWith = signal
    this.exitCode = 0
    return true
  }
}

function validConfig(overrides: Partial<BackendLauncherConfig> = {}): BackendLauncherConfig {
  return {
    ...DEFAULT_BACKEND_LAUNCHER_CONFIG,
    backendPath: '/home/test/distribllm/backend',
    syncDependencies: false,
    initialPeers: ['/ip4/203.0.113.10/tcp/7001/p2p/QmRelay'],
    trustedRelays: ['/ip4/203.0.113.10/tcp/7001/p2p/QmRelay'],
    ...overrides
  }
}

function decodeWslScript(args: string[]): string {
  const command = args.at(-1) ?? ''
  const encoded = command.match(/printf '%s' '([A-Za-z0-9+/=]+)' \| base64 --decode \| bash/)?.[1]
  assert.ok(encoded, 'expected an encoded WSL script command')
  return Buffer.from(encoded, 'base64').toString('utf8')
}

function fakeRuntime(
  overrides: Partial<BackendLauncherRuntime> = {}
): BackendLauncherRuntime & { child: FakeChild; calls: Array<[string, string[]]> } {
  const child = new FakeChild()
  const calls: Array<[string, string[]]> = []
  let healthChecks = 0
  return {
    platform: 'win32',
    child,
    calls,
    run: async (file, args): Promise<CommandResult> => {
      calls.push([file, args])
      if (args.includes('--list')) {
        return {
          stdout: '  NAME      STATE           VERSION\n* Ubuntu    Running         2\n',
          stderr: ''
        }
      }
      if (args.includes('-lc') && decodeWslScript(args).includes('wslpath -a -u')) {
        return { stdout: '/mnt/c/Program Files/DistribLLM/resources/backend-runtime\n', stderr: '' }
      }
      if (args.includes('-lc') && decodeWslScript(args).includes('runtime_source=')) {
        return {
          stdout: `/home/test/.local/state/distribllm/runtimes/${TEST_COMMIT}/backend\n`,
          stderr: ''
        }
      }
      if (args.includes('-lc') && decodeWslScript(args).includes('.distribllm-source-commit')) {
        return { stdout: `${TEST_COMMIT}\nclean\n`, stderr: '' }
      }
      if (args.includes('-lc') && decodeWslScript(args).includes('git rev-parse HEAD')) {
        return { stdout: `${TEST_COMMIT}\nclean\n`, stderr: '' }
      }
      return { stdout: '', stderr: '' }
    },
    spawn: (file, args) => {
      calls.push([file, args])
      return child
    },
    health: async () => {
      healthChecks += 1
      return healthChecks > 1
    },
    sleep: async () => undefined,
    now: () => '2026-08-12T00:00:00.000Z',
    ...overrides
  }
}

test('parses null-delimited WSL distro output', () => {
  assert.deepEqual(
    parseWslDistros('\uFEFFU\u0000b\u0000u\u0000n\u0000t\u0000u\u0000\r\u0000\n\u0000'),
    ['Ubuntu']
  )
})

test('parses selected distro names and WSL versions', () => {
  assert.deepEqual(
    parseWslDistroInfo(
      '  NAME            STATE           VERSION\n* Ubuntu         Running         2\n  Debian         Stopped         1\n'
    ),
    [
      { name: 'Ubuntu', version: 2 },
      { name: 'Debian', version: 1 }
    ]
  )
})

test('packaged defaults use the verified project VPS for bootstrap and relay', () => {
  assert.equal(
    PROJECT_VPS_RELAY_MADDR,
    '/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y'
  )
  assert.equal(DEFAULT_BACKEND_LAUNCHER_CONFIG.networkMode, 'auto')
  assert.deepEqual(DEFAULT_BACKEND_LAUNCHER_CONFIG.initialPeers, [PROJECT_VPS_RELAY_MADDR])
  assert.deepEqual(DEFAULT_BACKEND_LAUNCHER_CONFIG.trustedRelays, [PROJECT_VPS_RELAY_MADDR])
})

test('hashes the selected packaged artifact without exposing its path', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'distribllm-artifact-'))
  const artifactPath = join(directory, 'DistribLLM-portable.exe')
  try {
    await writeFile(artifactPath, 'verified artifact')
    const identity = await readArtifactIdentity(artifactPath)

    assert.equal(
      resolvePackagedArtifactPath('/extracted/DistribLLM.exe', artifactPath),
      artifactPath
    )
    assert.deepEqual(identity, {
      fileName: 'DistribLLM-portable.exe',
      sha256: '2127de9293abf1503418b9f78b3d530cdd2263417064815ee46b7ecdf1215ddc',
      bytes: 17
    })
    assert.doesNotMatch(JSON.stringify(identity), /distribllm-artifact-/)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('renderer trust is exact for packaged files and development origins', () => {
  const packaged = 'distribllm://app/index.html'

  assert.equal(isTrustedRendererUrl(packaged, packaged), true)
  assert.equal(isTrustedRendererUrl(`${packaged}#injected`, packaged), false)
  assert.equal(isTrustedRendererUrl('file:///tmp/attacker.html', packaged), false)
  assert.equal(isTrustedRendererUrl('distribllm://evil/index.html', packaged), false)
  assert.equal(
    isTrustedRendererUrl('http://localhost:5173/settings', packaged, 'http://localhost:5173'),
    true
  )
  assert.equal(
    isTrustedRendererUrl('http://localhost:5174/settings', packaged, 'http://localhost:5173'),
    false
  )
})

test('packaged renderer protocol rejects host confusion and path traversal', () => {
  const root = '/opt/distribllm/renderer'

  assert.equal(
    resolveRendererAssetPath(root, 'distribllm://app/assets/index.js'),
    '/opt/distribllm/renderer/assets/index.js'
  )
  assert.equal(resolveRendererAssetPath(root, 'distribllm://app/%2e%2e/secret.txt'), null)
  assert.equal(resolveRendererAssetPath(root, 'distribllm://attacker/assets/index.js'), null)
})

test('external navigation and CSP stay scoped to reviewed destinations', async () => {
  assert.equal(isTrustedExternalUrl('https://huggingface.co/facebook/opt-125m'), true)
  assert.equal(isTrustedExternalUrl('https://hf.co/model'), true)
  assert.equal(isTrustedExternalUrl('http://huggingface.co/model'), false)
  assert.equal(isTrustedExternalUrl('https://huggingface.co.evil.example/model'), false)
  const rendererHtml = await readFile('src/renderer/index.html', 'utf8')
  assert.doesNotMatch(rendererHtml, /unsafe-eval|script-src[^;]*unsafe-inline/)
  assert.match(rendererHtml, /object-src 'none'/)
})

test('requires a safe local backend configuration', () => {
  const errors = validateBackendLauncherConfig(
    validConfig({
      distroName: 'Ubuntu; shutdown',
      backendPath: '~/relative/backend',
      backendUrl: 'https://example.com',
      networkMode: 'relay',
      trustedRelays: []
    })
  )

  assert.ok(errors.some((error) => error.includes('distro name')))
  assert.ok(errors.some((error) => error.includes('absolute WSL path')))
  assert.ok(errors.some((error) => error.includes('loopback')))
  assert.ok(errors.some((error) => error.includes('trusted relay')))
})

test('packaged runtime does not require a developer backend path', () => {
  assert.deepEqual(
    validateBackendLauncherConfig(
      validConfig({ backendPath: '', useDeveloperBackendOverride: false }),
      true
    ),
    []
  )
  assert.ok(
    validateBackendLauncherConfig(
      validConfig({ backendPath: '', useDeveloperBackendOverride: true }),
      true
    ).some((error) => error.includes('developer override'))
  )
})

test('rejects malformed persisted configuration without throwing', () => {
  const malformed = {
    ...validConfig(),
    backendPath: 42,
    backendUrl: null,
    initialPeers: 'not-a-list'
  } as unknown as BackendLauncherConfig

  assert.doesNotThrow(() => validateBackendLauncherConfig(malformed))
  assert.ok(validateBackendLauncherConfig(malformed).length >= 3)
})

test('requires bootstrap and relay addresses for packaged auto mode', () => {
  const errors = validateBackendLauncherConfig(
    validConfig({ initialPeers: [], trustedRelays: [], networkMode: 'auto' })
  )

  assert.ok(errors.some((error) => error.includes('bootstrap peer')))
  assert.ok(errors.some((error) => error.includes('trusted relay')))
  assert.deepEqual(
    validateBackendLauncherConfig(
      validConfig({ initialPeers: [], trustedRelays: [], networkMode: 'direct' })
    ),
    []
  )
})

test('quotes backend paths and passes relay configuration through env', () => {
  const script = buildBackendLaunchScript(
    validConfig({ backendPath: "/home/test/distrib'llm/backend", relayWaitTimeoutSeconds: 120 })
  )

  assert.match(script, /export XDG_CACHE_HOME="\$\{XDG_CACHE_HOME:-\$HOME\/\.cache\}"/)
  assert.match(script, /export UV_CACHE_DIR="\$\{UV_CACHE_DIR:-\$XDG_CACHE_HOME\/uv\}"/)
  assert.match(
    script,
    /mkdir -p "\$XDG_CACHE_HOME" "\$XDG_CONFIG_HOME" "\$XDG_STATE_HOME" "\$UV_CACHE_DIR"/
  )
  assert.match(script, /cd -- '\/home\/test\/distrib'"'"'llm\/backend'/)
  assert.match(script, /DISTRIBLLM_NETWORK_MODE='auto'/)
  assert.match(script, /DISTRIBLLM_RELAY_WAIT_TIMEOUT='120'/)
  assert.match(script, /backend\.pid/)
})

test('sync and stop scripts guard WSL runtime directories before using uv or pid files', () => {
  const syncScript = buildDependencySyncScript(validConfig())
  const stopScript = buildBackendStopScript()

  assert.match(syncScript, /export XDG_CACHE_HOME="\$\{XDG_CACHE_HOME:-\$HOME\/\.cache\}"/)
  assert.match(syncScript, /export UV_CACHE_DIR="\$\{UV_CACHE_DIR:-\$XDG_CACHE_HOME\/uv\}"/)
  assert.match(
    syncScript,
    /mkdir -p "\$XDG_CACHE_HOME" "\$XDG_CONFIG_HOME" "\$XDG_STATE_HOME" "\$UV_CACHE_DIR"/
  )
  assert.match(syncScript, /uv sync --frozen --python 3\.12/)
  assert.match(stopScript, /export XDG_STATE_HOME="\$\{XDG_STATE_HOME:-\$HOME\/\.local\/state\}"/)
  assert.match(stopScript, /pid_file="\$XDG_STATE_HOME\/distribllm\/backend\.pid"/)
})

test('packaged runtime sync and launch use the explicit versioned environment', () => {
  const sourceCommit = 'c'.repeat(40)
  const syncScript = buildDependencySyncScript(validConfig(), sourceCommit)
  const launchScript = buildBackendLaunchScript(validConfig(), sourceCommit)

  assert.match(
    syncScript,
    new RegExp(`UV_PROJECT_ENVIRONMENT="\\$XDG_STATE_HOME/distribllm/environments/${sourceCommit}"`)
  )
  assert.match(syncScript, /export VIRTUAL_ENV="\$UV_PROJECT_ENVIRONMENT"/)
  assert.match(syncScript, /uv venv --python 3\.12 "\$UV_PROJECT_ENVIRONMENT"/)
  assert.match(syncScript, /uv sync --frozen --active --python 3\.12/)
  assert.match(launchScript, /"\$UV_PROJECT_ENVIRONMENT\/bin\/python" main\.py/)
  assert.doesNotMatch(launchScript, /uv run --frozen --no-sync/)
})

test('source identity script reports the tracked-clean backend Git revision', () => {
  const script = buildBackendSourceIdentityScript(validConfig())

  assert.match(script, /git rev-parse HEAD/)
  assert.match(script, /git status --porcelain --untracked-files=no/)
  assert.doesNotMatch(script, /git status --porcelain\s*$/m)
})

test('packaged runtime install is versioned, integrity checked, and atomic', () => {
  const script = buildPackagedBackendInstallScript(
    '/mnt/c/Program Files/DistribLLM/backend',
    TEST_COMMIT,
    TEST_MANIFEST_SHA256
  )

  assert.match(script, /runtime_root=.*distribllm\/runtimes/)
  assert.match(script, /sha256sum --check --strict/)
  assert.match(script, new RegExp(TEST_MANIFEST_SHA256))
  assert.match(script, /mktemp -d/)
  assert.match(script, /mv "\$staging_dir" "\$runtime_dir"/)
  assert.match(script, /chmod -R a-w/)
  assert.doesNotMatch(script, /git (pull|clone)/)
})

test('transports multiline WSL scripts without relying on Windows preserving newlines', () => {
  const script = 'set -eu\nexport EXAMPLE="one two"\nprintf "%s\\n" "$EXAMPLE"'
  const args = buildWslBashArgs('Ubuntu', script)

  assert.deepEqual(args.slice(0, 5), ['--distribution', 'Ubuntu', '--', 'bash', '-lc'])
  assert.equal(decodeWslScript(args), script)
  assert.doesNotMatch(args.at(-1) ?? '', /set -eu|export EXAMPLE/)
})

test('transports packaged Windows paths inside the encoded WSL script', () => {
  const windowsPath =
    'C:\\Users\\albad\\AppData\\Local\\Temp\\portable folder\\resources\\backend-runtime'
  const args = buildWslBashArgs('Ubuntu', buildWindowsPathConversionScript(windowsPath))
  const script = decodeWslScript(args)

  assert.match(script, /wslpath -a -u "\$windows_path"/)
  assert.ok(script.includes(`windows_path='${windowsPath}'`))
  assert.equal(args.includes(windowsPath), false)
  assert.equal(args.includes('wslpath'), false)
})

test('reports missing WSL without attempting a launch', async () => {
  const runtime = fakeRuntime({
    run: async () => {
      throw new Error('spawn wsl.exe ENOENT')
    }
  })
  const launcher = new WslBackendLauncher(validConfig(), runtime)

  const status = await launcher.start()

  assert.equal(status.state, 'missing_wsl')
  assert.equal(status.diagnosticCode, 'wsl_missing')
  assert.equal(runtime.child.listenerCount('exit'), 0)
})

test('reports a missing configured distro with installed distro evidence', async () => {
  const runtime = fakeRuntime({
    run: async (_file, args) =>
      args.includes('--list')
        ? {
            stdout:
              '  NAME            STATE           VERSION\n  Debian          Stopped         2\n  Ubuntu-24.04    Running         2\n',
            stderr: ''
          }
        : { stdout: '', stderr: '' }
  })
  const launcher = new WslBackendLauncher(validConfig({ distroName: 'Ubuntu' }), runtime)

  const status = await launcher.start()

  assert.equal(status.state, 'missing_distro')
  assert.equal(status.diagnosticCode, 'distro_missing')
  assert.match(status.detail ?? '', /Debian, Ubuntu-24\.04/)
})

test('rejects a selected distro that still uses WSL 1', async () => {
  const runtime = fakeRuntime({
    run: async (_file, args) =>
      args.includes('--list')
        ? {
            stdout: '  NAME      STATE           VERSION\n* Ubuntu    Running         1\n',
            stderr: ''
          }
        : { stdout: '', stderr: '' }
  })
  const launcher = new WslBackendLauncher(validConfig(), runtime)

  const status = await launcher.start()

  assert.equal(status.state, 'failed')
  assert.equal(status.diagnosticCode, 'distro_not_wsl2')
  assert.match(status.detail ?? '', /Current version: 1/)
})

test('reaches ready after WSL checks, process launch, and health success', async () => {
  const runtime = fakeRuntime()
  const launcher = new WslBackendLauncher(validConfig(), runtime)
  const states: string[] = []
  launcher.on('status', (status) => states.push(status.state))

  const status = await launcher.start()

  assert.equal(status.state, 'ready')
  assert.deepEqual(states, ['checking', 'starting_backend', 'ready'])
  const launch = runtime.calls.find(([, args]) => args.includes('bash') && args.includes('-lc'))
  assert.ok(launch)
  assert.match(
    decodeWslScript(launch[1]),
    /uv run --frozen --no-sync --python 3\.12 python main\.py/
  )
})

test('rejects an occupied backend port before starting a managed process', async () => {
  const runtime = fakeRuntime({ health: async () => true })
  const launcher = new WslBackendLauncher(validConfig(), runtime)

  const status = await launcher.start()

  assert.equal(status.state, 'failed')
  assert.equal(status.diagnosticCode, 'backend_port_conflict')
  assert.equal(runtime.child.listenerCount('exit'), 0)
})

test('stops the WSL PID and releases the launcher child', async () => {
  const runtime = fakeRuntime()
  const launcher = new WslBackendLauncher(validConfig(), runtime)
  await launcher.start()

  const status = await launcher.stop()

  assert.equal(status.state, 'idle')
  assert.equal(runtime.child.killedWith, 'SIGTERM')
  assert.ok(
    runtime.calls.some(
      ([, args]) => args.includes('-lc') && decodeWslScript(args).includes('kill -TERM')
    )
  )
})

test('does not restart after managed shutdown fails', async () => {
  let runCalls = 0
  const runtime = fakeRuntime({
    run: async (_file, args) => {
      runCalls += 1
      if (args.includes('--list')) {
        return {
          stdout: '  NAME      STATE           VERSION\n* Ubuntu    Running         2\n',
          stderr: ''
        }
      }
      if (args.includes('-lc') && decodeWslScript(args).includes('kill -TERM')) {
        throw new Error('WSL stopped responding')
      }
      return { stdout: '', stderr: '' }
    }
  })
  const launcher = new WslBackendLauncher(validConfig(), runtime)
  await launcher.start()
  const callsBeforeRestart = runCalls

  const status = await launcher.restart()

  assert.equal(status.state, 'failed')
  assert.equal(status.diagnosticCode, 'backend_stop_failed')
  assert.equal(runCalls, callsBeforeRestart + 1)
})

test('exports sanitized acceptance evidence after a complete packaged lifecycle', async () => {
  const config = validConfig({
    backendPath: '',
    useDeveloperBackendOverride: false,
    syncDependencies: true
  })
  const runtime = fakeRuntime()
  const launcher = new WslBackendLauncher(config, runtime, TEST_COMMIT, {
    windowsSourcePath: 'C:\\Program Files\\DistribLLM\\resources\\backend-runtime',
    sourceCommit: TEST_COMMIT,
    manifestSha256: TEST_MANIFEST_SHA256
  })

  await launcher.start()
  await launcher.stop()
  const report = launcher.getAcceptanceReport({
    ...packagedApplication()
  })

  assert.equal(report.ok, true)
  assert.equal(report.checks.backendHealthReady, true)
  assert.equal(report.checks.backendStoppedCleanly, true)
  assert.equal(report.checks.sourceCommitIdentified, true)
  assert.equal(report.checks.backendSourceMatchesApplication, true)
  assert.equal(report.checks.packagedBackendInstalled, true)
  assert.equal(report.checks.artifactIdentified, true)
  assert.equal(report.configuration.developerBackendOverrideConfigured, false)
  assert.equal(report.configuration.initialPeerCount, 1)
  assert.equal(report.launcher.currentStatus.state, 'idle')
  assert.equal(report.schemaVersion, 4)
  assert.equal(report.backendRuntime.sourceCommit, TEST_COMMIT)
  assert.equal(report.backendRuntime.sourceClean, true)
  assert.equal(report.backendRuntime.kind, 'packaged')
  assert.deepEqual(
    report.launcher.transitions.map((transition) => transition.state),
    [
      'idle',
      'checking',
      'installing_backend',
      'installing_backend',
      'starting_backend',
      'ready',
      'stopping',
      'idle'
    ]
  )
  const serialized = JSON.stringify(report)
  assert.doesNotMatch(serialized, /\/home\/test/)
  assert.doesNotMatch(serialized, /203\.0\.113\.10/)
})

test('acceptance report cannot pass outside a packaged Windows lifecycle', () => {
  const config = validConfig()
  const status = {
    state: 'ready' as const,
    message: 'Managed backend is ready.',
    diagnosticCode: null,
    detail: null,
    managed: false,
    updatedAt: '2026-08-13T00:00:00.000Z'
  }
  const report = buildWindowsAcceptanceReport(
    config,
    status,
    {
      transitions: [],
      wslAvailable: true,
      distroPresent: true,
      distroWsl2: true,
      dependencySyncRequested: true,
      dependencySyncCompleted: true,
      backendHealthReady: true,
      backendStoppedCleanly: true,
      backendSourceCommit: null,
      backendSourceClean: false,
      backendRuntimeKind: null
    },
    packagedApplication({
      packaged: false,
      platform: 'linux' as const,
      sourceDirty: true,
      artifactFileName: null,
      artifactSha256: null,
      artifactBytes: null
    }),
    '2026-08-13T00:00:00.000Z'
  )

  assert.equal(report.ok, false)
  assert.equal(report.checks.windowsHost, false)
  assert.equal(report.checks.packagedApplication, false)
  assert.equal(report.checks.sourceCommitIdentified, false)
  assert.equal(report.checks.backendSourceMatchesApplication, false)
  assert.equal(report.checks.packagedBackendInstalled, false)
  assert.equal(report.checks.artifactIdentified, false)
})

test('packaged launcher rejects a mismatched WSL backend before startup', async () => {
  const runtime = fakeRuntime({
    run: async (_file, args) => {
      runtime.calls.push([_file, args])
      if (args.includes('--list')) {
        return {
          stdout: '  NAME      STATE           VERSION\n* Ubuntu    Running         2\n',
          stderr: ''
        }
      }
      if (args.includes('-lc') && decodeWslScript(args).includes('git rev-parse HEAD')) {
        return { stdout: `${'c'.repeat(40)}\nclean\n`, stderr: '' }
      }
      return { stdout: '', stderr: '' }
    }
  })
  const launcher = new WslBackendLauncher(
    validConfig({ syncDependencies: true }),
    runtime,
    TEST_COMMIT
  )

  const status = await launcher.start()

  assert.equal(status.state, 'failed')
  assert.equal(status.diagnosticCode, 'backend_source_mismatch')
  assert.match(status.detail ?? '', new RegExp(TEST_COMMIT))
  assert.equal(runtime.child.listenerCount('exit'), 0)
  assert.equal(
    runtime.calls.some(
      ([, args]) => args.includes('-lc') && decodeWslScript(args).includes('uv sync')
    ),
    false
  )
})

test('changing launcher configuration clears evidence from the previous lifecycle', async () => {
  const runtime = fakeRuntime()
  const launcher = new WslBackendLauncher(validConfig({ syncDependencies: true }), runtime)
  await launcher.start()
  await launcher.stop()

  launcher.setConfig(validConfig({ syncDependencies: true, distroName: 'Ubuntu-24.04' }))
  const report = launcher.getAcceptanceReport({
    ...packagedApplication()
  })

  assert.equal(report.ok, false)
  assert.equal(report.configuration.distroName, 'Ubuntu-24.04')
  assert.equal(report.checks.wslAvailable, false)
  assert.deepEqual(
    report.launcher.transitions.map((transition) => transition.state),
    ['idle']
  )
})
