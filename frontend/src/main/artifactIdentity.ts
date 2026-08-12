import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import { basename } from 'node:path'

export interface ArtifactIdentity {
  fileName: string
  sha256: string
  bytes: number
}

export function resolvePackagedArtifactPath(
  execPath: string,
  portableExecutableFile?: string
): string {
  return portableExecutableFile?.trim() || execPath
}

export async function readArtifactIdentity(path: string): Promise<ArtifactIdentity> {
  const metadata = await stat(path)
  if (!metadata.isFile() || metadata.size <= 0) {
    throw new Error('Packaged application artifact is missing or empty.')
  }

  const hash = createHash('sha256')
  await new Promise<void>((resolve, reject) => {
    const stream = createReadStream(path)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('error', reject)
    stream.on('end', resolve)
  })

  return {
    fileName: basename(path),
    sha256: hash.digest('hex'),
    bytes: metadata.size
  }
}
