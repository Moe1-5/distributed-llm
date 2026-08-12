import { existsSync, statSync } from 'node:fs'
import { resolve } from 'node:path'
import asar from '@electron/asar'

const asarPath = resolve('dist', 'win-unpacked', 'resources', 'app.asar')
const portablePath = resolve('dist', 'DistribLLM-1.0.0-portable.exe')
for (const path of [asarPath, portablePath]) {
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

const portableSize = statSync(portablePath).size
if (portableSize === 0) throw new Error('Portable Windows artifact is empty.')

console.log(
  JSON.stringify(
    {
      asar_entries: entries.length,
      forbidden_entries: violations.length,
      portable_bytes: portableSize
    },
    null,
    2
  )
)
