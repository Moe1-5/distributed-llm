export type DiagnosticSeverity = 'info' | 'success' | 'warning' | 'error'
export type DiagnosticSource = 'api' | 'network' | 'inference' | 'backend'

export interface DiagnosticEvent {
  id: string
  capturedAt: string
  source: DiagnosticSource
  severity: DiagnosticSeverity
  summary: string
  details: Record<string, unknown>
  occurrences: number
}

export interface DiagnosticInput {
  source: DiagnosticSource
  severity: DiagnosticSeverity
  summary: string
  details?: Record<string, unknown>
}

const STORAGE_KEY = 'distribllm.diagnostics.v1'
const EVENT_LIMIT = 200
const MAX_TEXT_LENGTH = 2_000
const listeners = new Set<(events: DiagnosticEvent[]) => void>()

function makeId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function sanitizeValue(value: unknown, key = ''): unknown {
  if (/token|secret|password|authorization|cookie/i.test(key)) return '[redacted]'
  if (typeof value === 'string') return value.slice(0, MAX_TEXT_LENGTH)
  if (Array.isArray(value)) return value.slice(0, 50).map((item) => sanitizeValue(item))
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .slice(0, 50)
        .map(([childKey, childValue]) => [childKey, sanitizeValue(childValue, childKey)])
    )
  }
  return value
}

function safeDetails(details: Record<string, unknown> | undefined): Record<string, unknown> {
  return (sanitizeValue(details ?? {}) as Record<string, unknown>) ?? {}
}

function storageAvailable(): boolean {
  return typeof globalThis.localStorage !== 'undefined'
}

function normalizeStoredEvent(value: unknown): DiagnosticEvent | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<DiagnosticEvent>
  const sources: DiagnosticSource[] = ['api', 'network', 'inference', 'backend']
  const severities: DiagnosticSeverity[] = ['info', 'success', 'warning', 'error']
  if (
    typeof candidate.id !== 'string' ||
    typeof candidate.capturedAt !== 'string' ||
    !sources.includes(candidate.source as DiagnosticSource) ||
    !severities.includes(candidate.severity as DiagnosticSeverity) ||
    typeof candidate.summary !== 'string'
  ) {
    return null
  }
  return {
    id: candidate.id,
    capturedAt: candidate.capturedAt,
    source: candidate.source as DiagnosticSource,
    severity: candidate.severity as DiagnosticSeverity,
    summary: candidate.summary.slice(0, MAX_TEXT_LENGTH),
    details: safeDetails(candidate.details),
    occurrences:
      typeof candidate.occurrences === 'number' && candidate.occurrences > 0
        ? Math.floor(candidate.occurrences)
        : 1
  }
}

export function appendDiagnostic(
  events: DiagnosticEvent[],
  input: DiagnosticInput,
  capturedAt = new Date().toISOString(),
  id = makeId()
): DiagnosticEvent[] {
  const details = safeDetails(input.details)
  const summary = input.summary.slice(0, MAX_TEXT_LENGTH)
  const latest = events[events.length - 1]
  const duplicate =
    latest?.source === input.source &&
    latest.severity === input.severity &&
    latest.summary === summary &&
    JSON.stringify(latest.details) === JSON.stringify(details)

  if (duplicate) {
    return [...events.slice(0, -1), { ...latest, capturedAt, occurrences: latest.occurrences + 1 }]
  }

  return [
    ...events,
    {
      id,
      capturedAt,
      source: input.source,
      severity: input.severity,
      summary,
      details,
      occurrences: 1
    }
  ].slice(-EVENT_LIMIT)
}

export function getDiagnosticEvents(): DiagnosticEvent[] {
  if (!storageAvailable()) return []
  try {
    const parsed = JSON.parse(globalThis.localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(parsed)
      ? parsed
          .map(normalizeStoredEvent)
          .filter((event): event is DiagnosticEvent => event !== null)
          .slice(-EVENT_LIMIT)
      : []
  } catch {
    return []
  }
}

export function recordDiagnostic(input: DiagnosticInput): DiagnosticEvent {
  const next = appendDiagnostic(getDiagnosticEvents(), input)
  if (storageAvailable()) {
    try {
      globalThis.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // The in-page event still reaches subscribers if persistent storage is unavailable.
    }
  }
  listeners.forEach((listener) => listener(next))
  return next[next.length - 1]
}

export function clearDiagnosticEvents(): void {
  if (storageAvailable()) {
    try {
      globalThis.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // Subscribers still receive the cleared in-memory view.
    }
  }
  listeners.forEach((listener) => listener([]))
}

export function subscribeDiagnostics(listener: (events: DiagnosticEvent[]) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
