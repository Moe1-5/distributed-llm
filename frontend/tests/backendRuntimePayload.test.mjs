import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'

import { isBackendRuntimeFile, prepareBackendRuntime } from '../scripts/prepare-backend-runtime.mjs'

test('runtime allowlist includes application code and excludes tests and local state', () => {
  assert.equal(isBackendRuntimeFile('main.py'), true)
  assert.equal(isBackendRuntimeFile('api/server.py'), true)
  assert.equal(isBackendRuntimeFile('models/llama/block.py'), true)
  assert.equal(isBackendRuntimeFile('tests/test_generation_readiness.py'), false)
  assert.equal(isBackendRuntimeFile('.env'), false)
  assert.equal(isBackendRuntimeFile('.local_models.json'), false)
  assert.equal(isBackendRuntimeFile('traces/request.json'), false)
  assert.equal(isBackendRuntimeFile('model.zip'), false)
})

test('prepared runtime is commit-stamped and every payload file is checksummed', async () => {
  const outputDirectory = await mkdtemp(join(tmpdir(), 'distribllm-backend-runtime-'))
  try {
    const result = await prepareBackendRuntime({
      repositoryRoot: resolve('..'),
      outputDirectory,
      requireClean: false
    })
    assert.match(result.sourceCommit, /^[0-9a-f]{40}$/)
    assert.ok(result.files.includes('api/server.py'))
    assert.equal(
      await readFile(join(outputDirectory, '.distribllm-source-commit'), 'utf8'),
      `${result.sourceCommit}\n`
    )

    const manifest = await readFile(join(outputDirectory, '.distribllm-sha256'), 'utf8')
    for (const line of manifest.trim().split('\n')) {
      const match = line.match(/^([0-9a-f]{64}) {2}(.+)$/)
      assert.ok(match)
      const actual = createHash('sha256')
        .update(await readFile(join(outputDirectory, match[2])))
        .digest('hex')
      assert.equal(actual, match[1])
    }
    assert.doesNotMatch(manifest, /tests\/|\.env|traces\//)
  } finally {
    await rm(outputDirectory, { recursive: true, force: true })
  }
})
