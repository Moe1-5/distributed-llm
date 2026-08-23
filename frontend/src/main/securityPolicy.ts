import { isAbsolute, relative, resolve } from 'node:path'

const TRUSTED_EXTERNAL_HOSTS = new Set(['huggingface.co', 'hf.co'])

export function isTrustedExternalUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    return (
      parsed.protocol === 'https:' &&
      !parsed.username &&
      !parsed.password &&
      TRUSTED_EXTERNAL_HOSTS.has(parsed.hostname)
    )
  } catch {
    return false
  }
}

export function isTrustedRendererUrl(
  candidate: string,
  packagedFileUrl: string,
  developmentUrl?: string
): boolean {
  try {
    const parsedCandidate = new URL(candidate)
    if (developmentUrl) {
      const parsedDevelopment = new URL(developmentUrl)
      return (
        parsedCandidate.protocol === parsedDevelopment.protocol &&
        parsedCandidate.origin === parsedDevelopment.origin
      )
    }

    const parsedPackaged = new URL(packagedFileUrl)
    return (
      parsedCandidate.protocol === parsedPackaged.protocol &&
      parsedCandidate.host === parsedPackaged.host &&
      parsedCandidate.pathname === parsedPackaged.pathname &&
      !parsedCandidate.search &&
      !parsedCandidate.hash
    )
  } catch {
    return false
  }
}

export function resolveRendererAssetPath(root: string, requestUrl: string): string | null {
  try {
    if (/%2e/i.test(requestUrl) || /(?:^|\/)\.\.?\/?(?:$|[?#])/i.test(requestUrl)) {
      return null
    }
    const parsed = new URL(requestUrl)
    if (parsed.protocol !== 'distribllm:' || parsed.host !== 'app') return null
    const pathname = decodeURIComponent(parsed.pathname === '/' ? '/index.html' : parsed.pathname)
    if (pathname.includes('\0')) return null
    const target = resolve(root, `.${pathname}`)
    const child = relative(resolve(root), target)
    if (!child || child.startsWith('..') || isAbsolute(child)) return null
    return target
  } catch {
    return null
  }
}
