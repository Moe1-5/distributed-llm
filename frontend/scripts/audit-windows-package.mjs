/* eslint-disable @typescript-eslint/explicit-function-return-type -- executable Node script */
import { createHash } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import asar from '@electron/asar'

const asarPath = resolve('dist', 'win-unpacked', 'resources', 'app.asar')
const portablePath = resolve('dist', 'DistribLLM-1.0.0-portable.exe')
const backendRuntimePath = resolve('dist', 'win-unpacked', 'resources', 'backend-runtime')
for (const path of [asarPath, portablePath, backendRuntimePath]) {
  if (!existsSync(path)) {
    throw new Error(`Required Windows package artifact is missing: ${path}`)
  }
}

const entries = asar.listPackage(asarPath, { isPack: false }).filter(Boolean)
if (entries.length < 3) {
  throw new Error(`ASAR inspection returned too few entries: ${entries.length}`)
}
const forbidden = [
  /(^|\/)\.env($|\.)/i,
  /(^|\/).*\.zip$/i,
  /(^|\/)(models?|traces?|receipts?|identities|tokens?)(\/|$)/i,
  /huggingface.*token/i,
  /p2p.*identity/i
]
const violations = entries.filter((entry) => forbidden.some((pattern) => pattern.test(entry)))

if (violations.length > 0) {
  throw new Error(`Forbidden local or secret state was bundled:\n${violations.join('\n')}`)
}

const listRuntimeFiles = (directory, root = directory) => {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolutePath = join(directory, entry.name)
    if (lstatSync(absolutePath).isSymbolicLink()) {
      throw new Error(`Packaged backend contains a symbolic link: ${relative(root, absolutePath)}`)
    }
    return entry.isDirectory()
      ? listRuntimeFiles(absolutePath, root)
      : [relative(root, absolutePath).replaceAll('\\', '/')]
  })
}

const runtimeFiles = listRuntimeFiles(backendRuntimePath).sort()
const runtimeForbidden = [
  /(^|\/)\.env($|\.)/i,
  /(^|\/)(\.venv|tests?|traces?)(\/|$)/i,
  /(^|\/)\.(hf_token|local_models\.json)$/i,
  /\.(zip|tar|gz|7z|pyc|pyo|bin|safetensors)$/i,
  /(^|\/)__pycache__(\/|$)/i
]
const runtimeViolations = runtimeFiles.filter((entry) =>
  runtimeForbidden.some((pattern) => pattern.test(entry))
)
if (runtimeViolations.length > 0) {
  throw new Error(`Forbidden backend runtime state was bundled:\n${runtimeViolations.join('\n')}`)
}

const sourceCommit = readFileSync(
  join(backendRuntimePath, '.distribllm-source-commit'),
  'utf8'
).trim()
if (!/^[0-9a-f]{40}$/.test(sourceCommit)) {
  throw new Error('Packaged backend source commit marker is invalid.')
}
const expectedCommit = process.env.DISTRIBLLM_BUILD_SOURCE_COMMIT
if (expectedCommit && sourceCommit !== expectedCommit) {
  throw new Error(`Packaged backend commit ${sourceCommit} does not match ${expectedCommit}.`)
}

// electron-builder can refresh win-unpacked and its intermediate NSIS archive
// while leaving an older portable executable in dist. The unpacked runtime is
// not what a Windows participant launches, so never certify that stale wrapper.
const portableMtimeMs = statSync(portablePath).mtimeMs
const runtimeMarkerPath = join(backendRuntimePath, '.distribllm-source-commit')
const runtimeMarkerMtimeMs = statSync(runtimeMarkerPath).mtimeMs
if (portableMtimeMs < runtimeMarkerMtimeMs) {
  throw new Error(
    'Portable executable is older than the packaged backend runtime; rebuild it on a Windows-capable host before distributing it.'
  )
}

const checksumManifest = readFileSync(join(backendRuntimePath, '.distribllm-sha256'), 'utf8')
  .trim()
  .split('\n')
const manifestBytes = readFileSync(join(backendRuntimePath, '.distribllm-sha256'))
const manifestSha256 = createHash('sha256').update(manifestBytes).digest('hex')
const checksummedFiles = []
for (const line of checksumManifest) {
  const match = line.match(/^([0-9a-f]{64}) {2}([^\r\n]+)$/)
  if (!match) throw new Error(`Malformed backend runtime checksum entry: ${line}`)
  const [, expectedSha256, path] = match
  if (path.startsWith('/') || path.split('/').includes('..')) {
    throw new Error(`Unsafe backend runtime checksum path: ${path}`)
  }
  const actualSha256 = createHash('sha256')
    .update(readFileSync(join(backendRuntimePath, path)))
    .digest('hex')
  if (actualSha256 !== expectedSha256) {
    throw new Error(`Packaged backend checksum mismatch: ${path}`)
  }
  checksummedFiles.push(path)
}
const expectedRuntimeFiles = [...checksummedFiles, '.distribllm-sha256'].sort()
if (JSON.stringify(runtimeFiles) !== JSON.stringify(expectedRuntimeFiles)) {
  throw new Error('Packaged backend contains a file outside its checksum manifest.')
}
// The current ASAR library reports POSIX paths on Linux and backslash-prefixed
// paths on Windows, while extraction accepts neither spelling consistently
// across the two hosts. The source-identity values are static strings emitted
// into the Electron main bundle, so verify their presence in the archive bytes
// directly after structural ASAR inspection above.
const packagedAsarBytes = readFileSync(asarPath)
if (
  expectedCommit &&
  (!packagedAsarBytes.includes(Buffer.from(sourceCommit)) ||
    !packagedAsarBytes.includes(Buffer.from(manifestSha256)))
) {
  throw new Error('Electron main does not embed the expected backend commit and manifest digest.')
}

const portableSize = statSync(portablePath).size
if (portableSize === 0) throw new Error('Portable Windows artifact is empty.')

console.log(
  JSON.stringify(
    {
      asar_entries: entries.length,
      forbidden_entries: violations.length,
      backend_runtime_files: runtimeFiles.length,
      backend_runtime_commit: sourceCommit,
      backend_runtime_manifest_sha256: manifestSha256,
      backend_runtime_forbidden_entries: runtimeViolations.length,
      acceptance_identity_bound: Boolean(expectedCommit),
      portable_bytes: portableSize
    },
    null,
    2
  )
)
