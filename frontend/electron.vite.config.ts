import { resolve } from 'path'
import { createHash } from 'crypto'
import { existsSync, readFileSync } from 'fs'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const sourceCommit = process.env['DISTRIBLLM_BUILD_SOURCE_COMMIT'] ?? 'unknown'
const sourceDirty = process.env['DISTRIBLLM_BUILD_SOURCE_DIRTY'] !== 'false'
const backendManifestPath = resolve('build/backend-runtime/.distribllm-sha256')
const backendManifestSha256 = existsSync(backendManifestPath)
  ? createHash('sha256').update(readFileSync(backendManifestPath)).digest('hex')
  : 'unknown'

export default defineConfig({
  main: {
    define: {
      __DISTRIBLLM_SOURCE_COMMIT__: JSON.stringify(sourceCommit),
      __DISTRIBLLM_SOURCE_DIRTY__: JSON.stringify(sourceDirty),
      __DISTRIBLLM_BACKEND_MANIFEST_SHA256__: JSON.stringify(backendManifestSha256)
    }
  },
  preload: {},
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    plugins: [tailwindcss(), react()]
  }
})
