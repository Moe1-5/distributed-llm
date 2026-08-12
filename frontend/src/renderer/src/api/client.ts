/**
 * api/client.ts
 * Central API client for all backend communication.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
const WS_URL = import.meta.env.VITE_WS_BASE_URL ?? BASE_URL.replace(/^http/, 'ws')
const REQUEST_TIMEOUT_MS = 8_000

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface NodeInfo {
  peer_id: string
  rpc_uid?: string
  node_id?: string
  model_name: string
  layer_start: number
  layer_end: number
  device: string
  running: boolean
  maddrs: string[]
  layers_loaded: boolean
  rpc_running: boolean
  connection_mode?: 'checking' | 'direct' | 'relay'
  direct_reachability?: boolean | null
  transport_verified?: boolean
  loading?: {
    strategy: string
    architecture: string
    weight_format: string
    model_revision: string
    loaded_parameter_count: number
    loaded_parameter_bytes: number
    selected_checkpoint_bytes: number
    checkpoint_total_bytes: number | null
    source_shard_count: number
    elapsed_seconds: number
    peak_rss_delta_bytes: number
    fallback_reason?: string | null
  } | null
  rpc_safety?: {
    policy: {
      max_sequence_length: number
      max_batch_size: number
      max_tensor_bytes: number
      max_metadata_bytes: number
      max_concurrent_forwards: number
      max_queued_forwards: number
      queue_wait_seconds: number
      execution_timeout_seconds: number
    }
    accepted_requests: number
    completed_requests: number
    failed_requests: number
    rejected_requests: number
    rejected_by_reason: Record<string, number>
    timed_out_requests: number
    active_forwards: number
    peak_active_forwards: number
    queued_forwards: number
    peak_queued_forwards: number
  } | null
}

export interface LifecycleJob {
  job_id: string
  kind: 'node_start' | 'generator_start'
  resource_key: string
  status: 'queued' | 'running' | 'ready' | 'failed' | 'cancelled'
  stage: string
  detail: string
  elapsed_seconds: number
  cancel_requested: boolean
  result: Record<string, unknown> | null
  error: string | null
  reused?: boolean
}

export interface ModelInfo {
  id: string
  num_layers: number
  hidden_size: number
  gated: boolean
  tuning: 'base' | 'instruct' | 'chat'
  description: string
  vram_gb: number
  available: boolean
  local_imported: boolean
  local_import?: LocalModelImport
  runnable: boolean
  route_ready: boolean
  route_reasons: string[]
  covered_layers: number
  missing_layers: number[]
  total_layers: number
  compatible_nodes: number
  route_trace: string[]
}

export interface CoverageRange {
  start: number
  end: number
}

export interface CoverageSegment extends CoverageRange {
  provider_count: number
  status: 'missing' | 'covered' | 'redundant'
  recommended: boolean
}

export interface ServingRouteNode {
  peer_id: string
  node_id?: string | null
  rpc_uid: string
  layer_start: number
  layer_end: number
  recommended: boolean
}

export interface ServingRecommendation {
  layer_start: number
  layer_end: number
  newly_covered_layers: number
  adds_missing_coverage: boolean
  adds_redundancy: boolean
  completes_route: boolean
  reachable_prefix: number
  extends_reachable_prefix: boolean
}

export interface ServingPlan {
  model_id: string
  coverage_revision: string
  total_layers: number
  requested_layer_count: number
  segments: CoverageSegment[]
  missing_ranges: CoverageRange[]
  uncovered_ranges: CoverageRange[]
  projected_missing_ranges: CoverageRange[]
  recommendation: ServingRecommendation
  current_runnable: boolean
  projected_runnable: boolean
  reachable_prefix: number
  selected_route: ServingRouteNode[]
  projected_route: ServingRouteNode[]
  route_kind: 'unavailable' | 'single_provider' | 'multiple_providers'
  projected_route_kind: 'unavailable' | 'single_provider' | 'multiple_providers'
  standby_ranges: ServingRouteNode[]
}

export interface Stats {
  sampled_at?: string | null
  sample_interval_seconds?: number
  cpu_percent: number
  cpu_count?: number | null
  load_average_1m?: number | null
  ram_percent: number
  ram_used_gb: number
  ram_available_gb?: number
  ram_total_gb: number
  process?: {
    pid: number
    cpu_percent: number
    memory_percent: number
    rss_gb: number
    threads: number
  } | null
  gpu: {
    name: string
    util_percent: number | null
    vram_used_gb: number
    vram_reserved_gb?: number
    vram_total_gb: number
    vram_percent: number
  } | null
}

export interface NetworkStatus {
  status: string
  node_running: boolean
  node_info: NodeInfo | null
  node_infos?: NodeInfo[]
  local_node_ids?: string[]
  gpu_available: boolean
  generator_ready: boolean
  token_set: boolean
  local_models?: LocalModelImport[]
}

export interface IncentivesStatus {
  mode: 'off' | 'shadow' | 'credit'
  protocol_version: number
  application_public_key: string | null
  p2p_peer_id: string | null
  settlement_url_configured: boolean
  settlement_connectivity: 'disabled' | 'unconfigured' | 'idle' | 'connected' | 'error'
  pending_submissions: number
  accepted_submissions: number
  rejected_submissions: number
  last_error: string | null
  verified_credits: number
  ledger_entries: number
  accepted_receipts: number
  useful_positions_served: number
  token_ui_enabled: false
  transfers_enabled: false
  withdrawals_enabled: false
}

export interface GeneratorStatus {
  ready: boolean
  model_name: string | null
  route_ready: boolean
  reasons: string[]
  node_trace: string[]
  performance: GeneratorPerformance | null
  health: ProviderHealthStatus | null
}

export interface ProviderHealth {
  peer_id: string
  rpc_uid: string
  model_name: string
  model_revision: string
  layer_start: number
  layer_end: number
  state: 'checking' | 'healthy' | 'degraded' | 'offline'
  role: 'selected' | 'standby'
  dht_present: boolean
  protocol_compatible: boolean
  transport_verified: boolean | null
  consecutive_successes: number
  consecutive_failures: number
  last_probe_at: number | null
  last_success_at: number | null
  last_failure_at: number | null
  latency_ms: number | null
  reason: string | null
  next_probe_at: number
}

export interface ProviderHealthStatus {
  enabled: boolean
  monitor_running?: boolean
  route_ready: boolean
  health_revision: string
  detection_window_seconds?: number
  active_probes?: number
  last_discovery_error?: string | null
  reasons: string[]
  providers: ProviderHealth[]
  selected_providers?: ProviderHealth[]
  standby_providers?: ProviderHealth[]
}

export interface HopPerformance {
  peer_id: string
  rpc_uid: string
  layer_start: number
  layer_end: number
  calls: number
  total_latency_ms: number
  average_latency_ms: number
  last_latency_ms: number
}

export interface GenerationPerformance {
  time_to_first_token_ms: number | null
  total_duration_ms: number
  generated_tokens: number
  tokens_per_second: number
  route_validation_ms_total: number
  stopped: boolean
  hop_metrics: HopPerformance[]
}

export interface GeneratorPerformance {
  startup_duration_ms: number | null
  load_duration_ms: number | null
  route_validation_ms?: number | null
  last_generation: GenerationPerformance | null
}

export interface AppSettings {
  token_set: boolean
  token_preview: string | null
  local_models: LocalModelImport[]
  huggingface?: HuggingFaceConnection
}

export interface HuggingFaceConnection {
  configured: boolean
  connected: boolean
  username: string | null
  token_preview: string | null
  scope: string
  client_id_set: boolean
}

export interface HuggingFaceDeviceFlow {
  flow_id: string
  user_code: string
  verification_uri: string
  verification_uri_complete?: string | null
  expires_in: number
  interval: number
  scope: string
}

export interface HuggingFaceDevicePollResult {
  status: 'pending' | 'connected'
  error?: string
  message?: string
  interval?: number
  connection?: HuggingFaceConnection
}

export interface HuggingFaceDownloadJob {
  job_id: string
  model_name: string
  revision?: string | null
  status: 'queued' | 'downloading' | 'completed' | 'failed' | 'cancelled'
  message?: string | null
  error?: string | null
  cancel_requested: boolean
  created_at: string
  updated_at: string
  completed_at?: string | null
  model?: LocalModelImport | null
}

export interface LocalModelImport {
  model_name: string
  gated: boolean
  valid: boolean
  validated_at: string
  config: {
    model_type?: string
    architectures?: string[]
    num_layers?: number
    hidden_size?: number
  }
  tokenizer_files: string[]
  weight_format?: string
  weight_file_count: number
  sharded: boolean
  path?: string
}

export interface LocalModelValidationResult extends Partial<LocalModelImport> {
  valid: boolean
  model_name: string
  path?: string
  message?: string
  error?: string
}

export interface LocalModelRemoveResult {
  status: 'removed' | 'not_found'
  removed: boolean
  registry_removed: boolean
  files_deleted: boolean
  deleted_bytes: number
  deleted_size: string
  message: string
}

export interface TokenValidationResult {
  valid: boolean
  model_name: string
  gated: boolean
  token_required: boolean
  access_granted: boolean
  message: string
  error?: string
}

export interface NodeStartParams {
  model_name: string
  layer_start: number
  layer_end: number
  dht_prefix: string
  initial_peers: string[]
  device: string
  coverage_revision?: string
  confirm_redundancy?: boolean
}

export interface GeneratorStartParams {
  model_name: string
  dht_prefix: string
  initial_peers: string[]
}

export interface GenerationOptions {
  maxNewTokens?: number
  temperature?: number
  topP?: number
  topK?: number
  repetitionPenalty?: number
  doSample?: boolean
}

export interface GenerationTraceResult {
  prompt: string
  model_name: string
  generation_config: {
    max_new_tokens: number
    temperature: number
    top_p: number
    top_k: number
    repetition_penalty: number
    do_sample: boolean
  }
  response: string
  response_contains_replacement_char: boolean
  steps: Array<{
    step: number
    token_id: number
    token_text: string
    token_text_contains_replacement_char: boolean
    decoded_output_so_far: string
    decoded_output_contains_replacement_char: boolean
    selected_in_top_candidates: boolean
    top_candidates: Array<{
      token_id: number
      token_text: string
      token_text_contains_replacement_char: boolean
      logit: number
    }>
  }>
  node_trace: string[]
  trace_id: string
  trace_file: string
  trace_created_at: string
}

export interface GenerationTraceAnalysis {
  trace_dir: string
  model_name: string | null
  trace_count: number
  error_count: number
  errors: Array<{ trace_file: string; error: string }>
  groups: Array<{
    model_name: string | null
    prompt: string | null
    generation_config: Record<string, unknown>
    trace_ids: Array<string | null>
    trace_count: number
  }>
  summaries: Array<{
    trace_id: string | null
    trace_file: string
    created_at: string | null
    model_name: string | null
    prompt: string | null
    prompt_token_ids: number[]
    selected_token_ids: number[]
    selected_token_texts: string[]
    response: string
    step_count: number
    route_shape: {
      node_trace: string[]
      hop_count: number
      hidden_shapes: Array<{ step: number | null; before?: number[]; after?: number[] }>
    }
  }>
  discrepancy_counts: {
    cross_run: number
    replacement_char: number
    selected_outside_top_candidates: number
  }
  discrepancies: {
    cross_run: Array<{
      trace_id: string | null
      baseline_trace_id: string | null
      categories: string[]
    }>
    replacement_char_trace_ids: Array<string | null>
    selected_outside_top_candidates: Array<{
      trace_id: string | null
      steps: Array<{ step: number | null; token_id: number | null; token_text: string | null }>
    }>
  }
}

function generationOptionsPayload(options: GenerationOptions): Record<string, unknown> {
  return {
    ...(options.maxNewTokens !== undefined && { max_new_tokens: options.maxNewTokens }),
    ...(options.temperature !== undefined && { temperature: options.temperature }),
    ...(options.topP !== undefined && { top_p: options.topP }),
    ...(options.topK !== undefined && { top_k: options.topK }),
    ...(options.repetitionPenalty !== undefined && {
      repetition_penalty: options.repetitionPenalty
    }),
    ...(options.doSample !== undefined && { do_sample: options.doSample })
  }
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

async function fetchWithDeadline(path: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    return await fetch(`${BASE_URL}${path}`, { ...init, signal: controller.signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`${init?.method ?? 'GET'} ${path} timed out after 8 seconds`)
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetchWithDeadline(path)
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`)
  return res.json() as Promise<T>
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly payload: unknown
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetchWithDeadline(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  })
  if (!res.ok) {
    let detail = ''
    let errorPayload: unknown = null
    try {
      const payload = (await res.json()) as { detail?: unknown; error?: unknown; message?: unknown }
      errorPayload = payload
      const rawDetail = payload.detail ?? payload.error ?? payload.message
      if (typeof rawDetail === 'string') {
        detail = `: ${rawDetail}`
      } else if (rawDetail && typeof rawDetail === 'object') {
        const nested = rawDetail as { message?: unknown; error?: unknown }
        const message = nested.message ?? nested.error
        detail = typeof message === 'string' ? `: ${message}` : ''
      }
    } catch {
      detail = ''
    }
    throw new ApiError(`POST ${path} failed: ${res.status}${detail}`, res.status, errorPayload)
  }
  return res.json() as Promise<T>
}

async function del<T>(path: string): Promise<T> {
  const res = await fetchWithDeadline(path, { method: 'DELETE' })
  if (!res.ok) {
    let detail = ''
    try {
      const payload = (await res.json()) as { detail?: unknown; error?: unknown; message?: unknown }
      const rawDetail = payload.detail ?? payload.error ?? payload.message
      if (typeof rawDetail === 'string') {
        detail = `: ${rawDetail}`
      } else if (rawDetail && typeof rawDetail === 'object') {
        const nested = rawDetail as { message?: unknown; error?: unknown }
        const message = nested.message ?? nested.error
        detail = typeof message === 'string' ? `: ${message}` : ''
      }
    } catch {
      detail = ''
    }
    throw new Error(`DELETE ${path} failed: ${res.status}${detail}`)
  }
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
  getLocalNodes: () => get<{ nodes: NodeInfo[] }>('/nodes/local'),
  getIncentives: () => get<IncentivesStatus>('/incentives/accounting'),

  // Models — validated list from server, used for dropdown
  getModels: () =>
    get<{
      models: ModelInfo[]
      token_available: boolean
      default_peers: string[]
    }>('/models'),
  getModelCatalog: () =>
    get<{
      models: ModelInfo[]
      token_available: boolean
      default_peers: string[]
    }>('/models/catalog'),
  getServingPlan: (modelId: string, layerCount: number) =>
    get<ServingPlan>(
      `/models/${encodeURIComponent(modelId)}/serving-plan?layer_count=${encodeURIComponent(layerCount)}`
    ),

  // Node management
  startNode: (params: NodeStartParams) =>
    post<{ status: string; info?: NodeInfo; error?: string; message?: string }>(
      '/node/start',
      params
    ),
  startNodeAsync: (params: NodeStartParams) =>
    post<LifecycleJob>('/node/start-async', params),
  turnOnNode: (nodeId?: string) =>
    post<{ status: string; info?: NodeInfo; error?: string }>(
      `/node/turn-on${nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : ''}`
    ),
  turnOffNode: (nodeId?: string) =>
    post<{ status: string; info?: NodeInfo; error?: string }>(
      `/node/turn-off${nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : ''}`
    ),
  deleteNode: (nodeId?: string) =>
    del<{ status: string; error?: string }>(
      `/node${nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : ''}`
    ),
  stopNode: () => post<{ status: string }>('/node/stop'),

  // Generator
  startGenerator: (params: GeneratorStartParams) =>
    post<{ status: string; error?: string; message?: string }>('/generator/start', params),
  startGeneratorAsync: (params: GeneratorStartParams) =>
    post<LifecycleJob>('/generator/start-async', params),
  stopGenerator: () => post<{ status: string }>('/generator/stop'),
  unloadGenerator: () => post<{ status: string }>('/generator/unload'),
  getGeneratorStatus: () => get<GeneratorStatus>('/generator/status'),
  getLifecycleJob: (jobId: string) =>
    get<LifecycleJob>(`/lifecycle/jobs/${encodeURIComponent(jobId)}`),
  cancelLifecycleJob: (jobId: string) =>
    del<LifecycleJob>(`/lifecycle/jobs/${encodeURIComponent(jobId)}`),

  // Chat
  chat: (
    message: string,
    maxNewTokensOrOptions?: number | GenerationOptions,
    temperature?: number,
    topP?: number
  ) => {
    const options =
      typeof maxNewTokensOrOptions === 'object'
        ? maxNewTokensOrOptions
        : { maxNewTokens: maxNewTokensOrOptions, temperature, topP }
    return post<{
      response: string
      node_trace: string[]
      tokens_generated: number
      performance: GenerationPerformance | null
      error?: string
    }>('/chat', {
      message,
      ...generationOptionsPayload(options)
    })
  },
  traceGeneration: (prompt: string, options: GenerationOptions = {}) =>
    post<GenerationTraceResult>('/generator/trace', {
      prompt,
      ...generationOptionsPayload(options)
    }),
  analyzeGenerationTraces: (modelName?: string) =>
    get<GenerationTraceAnalysis>(
      `/generator/traces/analysis${modelName ? `?model_name=${encodeURIComponent(modelName)}` : ''}`
    ),

  // Settings
  getSettings: () => get<AppSettings>('/settings'),
  getHuggingFaceConnection: () => get<HuggingFaceConnection>('/settings/huggingface/connection'),
  startHuggingFaceDeviceLogin: () =>
    post<HuggingFaceDeviceFlow>('/settings/huggingface/oauth/device'),
  pollHuggingFaceDeviceLogin: (flowId: string) =>
    post<HuggingFaceDevicePollResult>('/settings/huggingface/oauth/device/poll', {
      flow_id: flowId
    }),
  disconnectHuggingFace: () =>
    del<{ status: string; connection: HuggingFaceConnection }>('/settings/huggingface/connection'),
  downloadHuggingFaceModel: (modelName: string, revision?: string) =>
    post<{ status: string; job: HuggingFaceDownloadJob }>('/settings/huggingface/download', {
      model_name: modelName,
      ...(revision !== undefined && { revision })
    }),
  getHuggingFaceDownload: (jobId: string) =>
    get<{ job: HuggingFaceDownloadJob }>(
      `/settings/huggingface/downloads/${encodeURIComponent(jobId)}`
    ),
  cancelHuggingFaceDownload: (jobId: string) =>
    del<{ job: HuggingFaceDownloadJob }>(
      `/settings/huggingface/downloads/${encodeURIComponent(jobId)}`
    ),
  getLocalModels: () => get<{ models: LocalModelImport[] }>('/settings/local-models'),
  inspectLocalModel: (modelName: string, path: string) =>
    post<LocalModelValidationResult>('/settings/local-models/inspect', {
      model_name: modelName,
      path
    }),
  importLocalModel: (modelName: string, path: string) =>
    post<{ status: string; model: LocalModelImport }>('/settings/local-models', {
      model_name: modelName,
      path
    }),
  removeLocalModel: (modelName: string, deleteFiles = false) =>
    del<LocalModelRemoveResult>(
      `/settings/local-models/${encodeURIComponent(modelName)}?delete_files=${deleteFiles ? 'true' : 'false'}`
    ),
  saveToken: (token: string) =>
    post<{ status: string; token_preview?: string }>('/settings/token', { token }),
  validateToken: (modelName: string, token?: string) =>
    post<TokenValidationResult>('/settings/token/validate', {
      model_name: modelName,
      ...(token !== undefined && { token })
    }),
  deleteToken: () => del<{ status: string }>('/settings/token')
}

// ---------------------------------------------------------------------------
// WebSocket streaming
// ---------------------------------------------------------------------------

export interface StreamChunk {
  token?: string
  done?: boolean
  node_trace?: string[]
  metrics?: GenerationPerformance
  error?: string
}

export function createStreamSocket(
  onToken: (token: string) => void,
  onDone: (trace: string[]) => void,
  onError: (error: string) => void,
  onOpen?: () => void,
  onClose?: () => void
): {
  send: (
    message: string,
    maxNewTokensOrOptions?: number | GenerationOptions,
    temperature?: number,
    topP?: number
  ) => void
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
    send: (
      message,
      maxNewTokensOrOptions?: number | GenerationOptions,
      temperature?: number,
      topP?: number
    ) => {
      const options =
        typeof maxNewTokensOrOptions === 'object'
          ? maxNewTokensOrOptions
          : { maxNewTokens: maxNewTokensOrOptions, temperature, topP }
      const payload = JSON.stringify({
        message,
        ...generationOptionsPayload(options)
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
