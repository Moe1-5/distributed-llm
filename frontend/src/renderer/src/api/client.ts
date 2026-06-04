/**
 * api/client.ts
 * Central API client for all backend communication.
 */

const BASE_URL = 'http://172.27.32.227:8000'
const WS_URL = 'ws://172.27.32.227:8000'

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

  // Chat
  chat: (message: string, maxNewTokens = 200, temperature = 0.7) =>
    post<{ response: string; node_trace: string[]; error?: string }>('/chat', {
      message,
      max_new_tokens: maxNewTokens,
      temperature
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
  onError: (error: string) => void
): {
  send: (message: string, maxNewTokens?: number, temperature?: number) => void
  close: () => void
} {
  const ws = new WebSocket(`${WS_URL}/stream`)

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

  return {
    send: (message, maxNewTokens = 200, temperature = 0.7) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ message, max_new_tokens: maxNewTokens, temperature }))
      } else {
        onError('WebSocket not connected')
      }
    },
    close: () => ws.close()
  }
}
