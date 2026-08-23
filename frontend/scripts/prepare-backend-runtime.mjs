/* eslint-disable @typescript-eslint/explicit-function-return-type -- executable Node script */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const RUNTIME_ROOT_FILES = new Set([
  '__init__.py',
  'constants.py',
  'main.py',
  'pyproject.toml',
  'uv.lock'
])
const RUNTIME_DIRECTORIES = [
  'api/',
  'client/',
  'incentives/',
  'models/',
  'network/',
  'node/',
  'placement/'
]
const REQUIRED_FILES = ['main.py', 'pyproject.toml', 'uv.lock', 'api/server.py', 'node/node.py']

export const isBackendRuntimeFile = (path) => {
  return (
    RUNTIME_ROOT_FILES.has(path) ||
    (RUNTIME_DIRECTORIES.some((directory) => path.startsWith(directory)) &&
      (path.endsWith('.py') || path.endsWith('.json')))
  )
}

const runGit = (repositoryRoot, args, options = {}) => {
  return execFileSync('git', args, {
    cwd: repositoryRoot,
    encoding: 'utf8',
    stdio: options.stdio ?? ['ignore', 'pipe', 'pipe']
  })
}

const resolveSourceCommit = (repositoryRoot, suppliedCommit) => {
  const headCommit = runGit(repositoryRoot, ['rev-parse', 'HEAD']).trim()
  const sourceCommit = suppliedCommit || headCommit
  if (!/^[0-9a-f]{40}$/.test(sourceCommit)) {
    throw new Error('Backend runtime packaging requires a full lowercase Git commit.')
  }
  if (sourceCommit !== headCommit) {
    throw new Error('Supplied backend runtime commit does not match the checked-out revision.')
  }
  return sourceCommit
}

const assertTrackedSourceClean = (repositoryRoot) => {
  try {
    runGit(repositoryRoot, ['diff', '--quiet', 'HEAD'], { stdio: 'ignore' })
  } catch {
    throw new Error('Refusing to stamp a clean package from modified tracked source.')
  }
}

const sha256 = async (path) => {
  return createHash('sha256')
    .update(await readFile(path))
    .digest('hex')
}

export const prepareBackendRuntime = async ({
  repositoryRoot,
  outputDirectory,
  suppliedCommit = '',
  requireClean = false
}) => {
  const sourceCommit = resolveSourceCommit(repositoryRoot, suppliedCommit)
  if (requireClean) assertTrackedSourceClean(repositoryRoot)

  const tracked = runGit(repositoryRoot, ['ls-files', '-z', '--', 'backend'])
    .split('\0')
    .filter(Boolean)
    .map((path) => path.replace(/^backend\//, ''))
  const files = tracked.filter(isBackendRuntimeFile).sort()
  for (const required of REQUIRED_FILES) {
    if (!files.includes(required))
      throw new Error(`Required backend runtime file is missing: ${required}`)
  }

  await rm(outputDirectory, { recursive: true, force: true })
  await mkdir(outputDirectory, { recursive: true })
  for (const path of files) {
    const destination = join(outputDirectory, path)
    await mkdir(dirname(destination), { recursive: true })
    await copyFile(join(repositoryRoot, 'backend', path), destination)
  }
  await writeFile(join(outputDirectory, '.distribllm-source-commit'), `${sourceCommit}\n`, 'utf8')

  const checksummedFiles = [...files, '.distribllm-source-commit'].sort()
  const manifestLines = []
  for (const path of checksummedFiles) {
    manifestLines.push(`${await sha256(join(outputDirectory, path))}  ${path}`)
  }
  await writeFile(
    join(outputDirectory, '.distribllm-sha256'),
    `${manifestLines.join('\n')}\n`,
    'utf8'
  )

  return { sourceCommit, files: checksummedFiles }
}

const scriptPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (scriptPath === fileURLToPath(import.meta.url)) {
  const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const repositoryRoot = resolve(frontendRoot, '..')
  const suppliedCommit = process.env.DISTRIBLLM_BUILD_SOURCE_COMMIT ?? ''
  const requireClean = process.env.DISTRIBLLM_BUILD_SOURCE_DIRTY === 'false'
  const result = await prepareBackendRuntime({
    repositoryRoot,
    outputDirectory: join(frontendRoot, 'build', 'backend-runtime'),
    suppliedCommit,
    requireClean
  })
  const relativeOutput = relative(repositoryRoot, join(frontendRoot, 'build', 'backend-runtime'))
  console.log(
    JSON.stringify({
      source_commit: result.sourceCommit,
      runtime_files: result.files.length,
      output: relativeOutput
    })
  )
}
