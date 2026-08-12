import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

const temporaryDirectory = mkdtempSync(join(tmpdir(), 'distribllm-renderer-flow-tests-'))
const outputFile = join(temporaryDirectory, 'independentRefresh.test.cjs')
const executable = resolve(
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'esbuild.cmd' : 'esbuild'
)

try {
  const build = spawnSync(
    executable,
    [
      'tests/independentRefresh.test.ts',
      '--bundle',
      '--platform=node',
      '--format=cjs',
      `--outfile=${outputFile}`
    ],
    { stdio: 'inherit' }
  )
  if (build.status !== 0) process.exit(build.status ?? 1)

  const test = spawnSync(process.execPath, [outputFile], { stdio: 'inherit' })
  process.exitCode = test.status ?? 1
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true })
}
