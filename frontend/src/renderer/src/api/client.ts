/**
 * api/client.ts
 * Central API client for all backend communication.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
const WS_URL = import.meta.env.VITE_WS_BASE_URL ?? BASE_URL.replace(/^http/, 'ws')

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface NodeInfo {
  peer_id: string
  model_name: string
  layer_start: number
  layer_end: number
  device: string
  running: boolean
  maddrs: string[]
  layers_loaded: boolean
  rpc_running: boolean
}

export interface ModelInfo {
  id: string
  num_layers: number
  hidden_size: number
  gated: boolean
  description: string
  vram_gb: number
  available: boolean
  runnable: boolean
  route_ready: boolean
  route_reasons: string[]
  covered_layers: number
  missing_layers: number[]
  total_layers: number
  compatible_nodes: number
  route_trace: string[]
}

export interface Stats {
  cpu_percent: number
  ram_percent: number
  ram_used_gb: number
  ram_total_gb: number
  gpu: {
    name: string
    util_percent: number
    vram_used_gb: number
    vram_total_gb: number
    vram_percent: number
  } | null
}

export interface NetworkStatus {
  status: string
  node_running: boolean
  node_info: NodeInfo | null
  gpu_available: boolean
  generator_ready: boolean
  token_set: boolean
}

export interface GeneratorStatus {
  ready: boolean
  model_name: string | null
  route_ready: boolean
  reasons: string[]
  node_trace: string[]
}

export interface AppSettings {
  token_set: boolean
  token_preview: string | null
}

export interface NodeStartParams {
  model_name: string
  layer_start: number
  layer_end: number
  dht_prefix: string
  initial_peers: string[]
  device: string
}

export interface GeneratorStartParams {
  model_name: string
  dht_prefix: string
  initial_peers: string[]
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`)
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  })
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`)
  return res.json() as Promise<T>
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`DELETE ${path} failed: ${res.status}`)
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export const api = {
  // Status
  getStatus: () => get<NetworkStatus>('/status'),
  getStats: () => get<Stats>('/stats'),
  getNodes: () => get<{ nodes: NodeInfo[]; error?: string; warning?: string }>('/nodes'),

  // Models — validated list from server, used for dropdown
  getModels: () =>
    get<{
      models: ModelInfo[]
      token_available: boolean
      default_peers: string[]
    }>('/models'),

  // Node management
  startNode: (params: NodeStartParams) =>
    post<{ status: string; info?: NodeInfo; error?: string; message?: string }>(
      '/node/start',
      params
    ),
  stopNode: () => post<{ status: string }>('/node/stop'),

  // Generator
  startGenerator: (params: GeneratorStartParams) =>
    post<{ status: string; error?: string; message?: string }>('/generator/start', params),
  stopGenerator: () => post<{ status: string }>('/generator/stop'),
  getGeneratorStatus: () => get<GeneratorStatus>('/generator/status'),

  // Chat
  chat: (message: string, maxNewTokens?: number, temperature?: number, topP?: number) =>
    post<{ response: string; node_trace: string[]; error?: string }>('/chat', {
      message,
      ...(maxNewTokens !== undefined && { max_new_tokens: maxNewTokens }),
      ...(temperature !== undefined && { temperature }),
      ...(topP !== undefined && { top_p: topP })
    }),

  // Settings
  getSettings: () => get<AppSettings>('/settings'),
  saveToken: (token: string) =>
    post<{ status: string; token_preview?: string }>('/settings/token', { token }),
  deleteToken: () => del<{ status: string }>('/settings/token')
}

// ---------------------------------------------------------------------------
// WebSocket streaming
// ---------------------------------------------------------------------------

export interface StreamChunk {
  token?: string
  done?: boolean
  node_trace?: string[]
  error?: string
}

export function createStreamSocket(
  onToken: (token: string) => void,
  onDone: (trace: string[]) => void,
  onError: (error: string) => void,
  onOpen?: () => void,
  onClose?: () => void
): {
  send: (message: string, maxNewTokens?: number, temperature?: number, topP?: number) => void
  close: () => void
} {
  const ws = new WebSocket(`${WS_URL}/stream`)
  const pendingPayloads: string[] = []
  let manuallyClosed = false

  function flushPending(): void {
    while (pendingPayloads.length > 0 && ws.readyState === WebSocket.OPEN) {
      const payload = pendingPayloads.shift()
      if (payload !== undefined) ws.send(payload)
    }
  }

  ws.onopen = () => {
    onOpen?.()
    flushPending()
  }

  ws.onmessage = (event: MessageEvent) => {
    try {
      const chunk = JSON.parse(event.data as string) as StreamChunk
      if (chunk.token !== undefined) onToken(chunk.token)
      else if (chunk.done) onDone(chunk.node_trace ?? [])
      else if (chunk.error) onError(chunk.error)
    } catch {
      onError('Failed to parse server message')
    }
  }

  ws.onerror = () => onError('WebSocket connection error')
  ws.onclose = () => {
    if (!manuallyClosed) onClose?.()
  }

  return {
    send: (message, maxNewTokens?: number, temperature?: number, topP?: number) => {
      const payload = JSON.stringify({
        message,
        ...(maxNewTokens !== undefined && { max_new_tokens: maxNewTokens }),
        ...(temperature !== undefined && { temperature }),
        ...(topP !== undefined && { top_p: topP })
      })

      if (ws.readyState === WebSocket.OPEN) {
        ws.send(payload)
      } else if (ws.readyState === WebSocket.CONNECTING) {
        pendingPayloads.push(payload)
      } else {
        onError('WebSocket not connected')
      }
    },
    close: () => {
      manuallyClosed = true
      ws.close()
    }
  }
}
