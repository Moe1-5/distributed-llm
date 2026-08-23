import type { GeneratorStatus, ModelInfo, NetworkRuntimeSnapshot, ServingPlan } from './client'
import { isServingPlanAuthoritative } from './servingPlanState'

export type PresentationTone = 'ready' | 'working' | 'waiting' | 'degraded' | 'failed' | 'inactive'

export interface ModelPresentation {
  primary: 'Can generate remotely' | 'Serving locally' | 'Needs providers' | 'Requires local access'
  secondary: string
  tone: PresentationTone
  action: string
}

export function modelPresentation(
  model: ModelInfo,
  plan: ServingPlan | null,
  localServing = false
): ModelPresentation {
  if (model.gated && !model.local_imported) {
    return {
      primary: 'Requires local access',
      secondary: 'Approved model files are not available on this device.',
      tone: 'failed',
      action: 'Download or import the approved model files.'
    }
  }

  if (plan && !isServingPlanAuthoritative(plan)) {
    return {
      primary: localServing ? 'Serving locally' : 'Needs providers',
      secondary: 'Remote provider discovery is still producing an authoritative snapshot.',
      tone: 'working',
      action: 'Wait for the coverage snapshot to become fresh.'
    }
  }

  if (plan?.current_runnable || model.availability?.can_generate_remotely || model.route_ready) {
    return {
      primary: 'Can generate remotely',
      secondary: localServing
        ? 'This device is also serving layers for a complete validated route.'
        : 'A complete compatible provider route is ready.',
      tone: 'ready',
      action: 'Start the inference client.'
    }
  }

  if (localServing || model.availability?.local_serving) {
    return {
      primary: 'Serving locally',
      secondary: 'This device contributes layers, but the network route is incomplete.',
      tone: 'degraded',
      action: 'Add providers for the missing layers.'
    }
  }

  return {
    primary: 'Needs providers',
    secondary:
      model.availability?.reason ??
      (plan
        ? `No complete route is available; ${plan.missing_ranges.length} range(s) are missing.`
        : 'Remote route availability has not loaded yet.'),
    tone: model.availability?.state === 'route_validating' ? 'working' : 'waiting',
    action:
      model.availability?.action ??
      (plan ? 'Start providers for the missing layers.' : 'Wait for discovery or refresh.')
  }
}

export interface RuntimeStage {
  id:
    | 'backend'
    | 'discovery'
    | 'rpc'
    | 'canary'
    | 'generator'
    | 'generation'
    | 'websocket'
    | 'diagnostics'
  label: string
  value: string
  tone: PresentationTone
  explanation: string
  action: string | null
}

export interface RuntimePresentationInput {
  backend: 'checking' | 'online' | 'offline'
  network?: NetworkRuntimeSnapshot | null
  generator?: GeneratorStatus | null
  stream: 'closed' | 'connecting' | 'open' | 'error'
  generationActive: boolean
  diagnostics: 'idle' | 'queued' | 'running' | 'ready' | 'failed' | 'cancelled'
}

function discoveryStage(network?: NetworkRuntimeSnapshot | null): RuntimeStage {
  if (!network) {
    return {
      id: 'discovery',
      label: 'DHT discovery',
      value: 'UNKNOWN',
      tone: 'waiting',
      explanation: 'This backend did not provide the network lifecycle contract.',
      action: 'Restart Electron and the backend from the same build.'
    }
  }
  if (network.state === 'ready' && !network.stale) {
    return {
      id: 'discovery',
      label: 'DHT discovery',
      value: 'FRESH',
      tone: 'ready',
      explanation: `${network.provider_count} compatible provider(s) are in the current snapshot.`,
      action: null
    }
  }
  if (network.state === 'syncing') {
    return {
      id: 'discovery',
      label: 'DHT discovery',
      value: 'SYNCING',
      tone: 'working',
      explanation: 'The control-plane supervisor is refreshing provider leases.',
      action: 'Wait for a fresh snapshot.'
    }
  }
  return {
    id: 'discovery',
    label: 'DHT discovery',
    value: network.state === 'degraded' ? 'DEGRADED' : 'OFFLINE',
    tone: network.state === 'degraded' ? 'degraded' : 'failed',
    explanation:
      network.failure?.message ??
      (network.stale
        ? 'Only the last known provider topology is available.'
        : 'DHT is disconnected.'),
    action: network.failure?.retryable
      ? 'Wait for supervisor recovery.'
      : 'Check bootstrap connectivity.'
  }
}

function rpcStage(generator?: GeneratorStatus | null): RuntimeStage {
  const health = generator?.health
  if (!generator?.components_loaded) {
    return {
      id: 'rpc',
      label: 'Provider RPC',
      value: 'INACTIVE',
      tone: 'inactive',
      explanation: 'Provider probes start with the inference client.',
      action: 'Start the inference client after discovery is ready.'
    }
  }
  if (!health) {
    return {
      id: 'rpc',
      label: 'Provider RPC',
      value: 'UNKNOWN',
      tone: 'waiting',
      explanation: 'No provider-health snapshot is available.',
      action: 'Wait for the first provider probe.'
    }
  }
  if (
    (health.active_probes ?? 0) > 0 ||
    health.providers.some((provider) => provider.state === 'checking')
  ) {
    return {
      id: 'rpc',
      label: 'Provider RPC',
      value: 'PROBING',
      tone: 'working',
      explanation: 'Tensor RPC health checks are still running.',
      action: 'Wait for the probes to complete.'
    }
  }
  if (health.route_ready) {
    return {
      id: 'rpc',
      label: 'Provider RPC',
      value: 'HEALTHY',
      tone: 'ready',
      explanation: 'Every selected provider passed the current RPC health policy.',
      action: null
    }
  }
  return {
    id: 'rpc',
    label: 'Provider RPC',
    value: 'UNHEALTHY',
    tone: 'failed',
    explanation: health.reasons.join('; ') || 'The selected provider route is not RPC healthy.',
    action: 'Inspect Monitoring provider health and the latest route failure.'
  }
}

export function runtimeStages(input: RuntimePresentationInput): RuntimeStage[] {
  const generator = input.generator
  const backend: RuntimeStage = {
    id: 'backend',
    label: 'Backend',
    value:
      input.backend === 'online' ? 'ONLINE' : input.backend === 'checking' ? 'CHECKING' : 'OFFLINE',
    tone:
      input.backend === 'online' ? 'ready' : input.backend === 'checking' ? 'working' : 'failed',
    explanation:
      input.backend === 'online'
        ? 'The local FastAPI backend is responding.'
        : input.backend === 'checking'
          ? 'Checking the local backend.'
          : 'The local backend is not responding.',
    action: input.backend === 'offline' ? 'Open Settings and restart the managed backend.' : null
  }
  const canary: RuntimeStage = !generator?.components_loaded
    ? {
        id: 'canary',
        label: 'Tensor canary',
        value: 'INACTIVE',
        tone: 'inactive',
        explanation: 'The canary runs during generator route validation.',
        action: null
      }
    : generator.canary?.ok
      ? {
          id: 'canary',
          label: 'Tensor canary',
          value: 'PASSED',
          tone: 'ready',
          explanation: 'A bounded tensor crossed the selected route successfully.',
          action: null
        }
      : {
          id: 'canary',
          label: 'Tensor canary',
          value: generator.canary ? 'FAILED' : 'WAITING',
          tone: generator.canary ? 'failed' : 'working',
          explanation: generator.canary?.reason ?? 'Waiting for route canary evidence.',
          action: generator.canary
            ? 'Inspect the failed provider and transport stage.'
            : 'Wait for validation.'
        }
  const generatorStage: RuntimeStage =
    generator?.state === 'ready' && generator.ready
      ? {
          id: 'generator',
          label: 'Generator',
          value: 'READY',
          tone: 'ready',
          explanation: 'Local components are loaded and the selected route is validated.',
          action: null
        }
      : generator?.state === 'suspended'
        ? {
            id: 'generator',
            label: 'Generator',
            value: 'SUSPENDED',
            tone: 'failed',
            explanation:
              generator.reasons.join('; ') || 'Loaded components no longer have a usable route.',
            action: 'Unload or restart the inference client after provider recovery.'
          }
        : {
            id: 'generator',
            label: 'Generator',
            value:
              generator?.state === 'ready'
                ? 'WAITING'
                : (generator?.state?.toUpperCase() ?? 'STOPPED'),
            tone:
              generator?.state === 'failed' ? 'failed' : generator?.state ? 'working' : 'inactive',
            explanation: generator?.reasons.join('; ') || 'The inference client is not ready.',
            action:
              generator?.state === 'failed' ? 'Return to Network and restart inference.' : null
          }
  const generation: RuntimeStage = {
    id: 'generation',
    label: 'Generation',
    value: input.generationActive ? 'ACTIVE' : 'IDLE',
    tone: input.generationActive ? 'working' : 'inactive',
    explanation: input.generationActive
      ? 'A user generation request is actively streaming tokens.'
      : 'No user generation request is active.',
    action: null
  }
  const websocket: RuntimeStage = {
    id: 'websocket',
    label: 'Local WebSocket',
    value: input.stream.toUpperCase(),
    tone:
      input.stream === 'open'
        ? 'ready'
        : input.stream === 'connecting'
          ? 'working'
          : input.stream === 'error'
            ? 'failed'
            : 'inactive',
    explanation:
      input.stream === 'open'
        ? 'The renderer-to-backend WebSocket is open for chat generation.'
        : input.stream === 'error'
          ? 'The local generation WebSocket closed with an error.'
          : input.stream === 'connecting'
            ? 'Opening the local generation WebSocket.'
            : 'No chat stream is open.',
    action: input.stream === 'error' ? 'Reopen the stream after checking the route failure.' : null
  }
  const diagnostics: RuntimeStage = {
    id: 'diagnostics',
    label: 'Legacy trace',
    value: input.diagnostics.toUpperCase(),
    tone:
      input.diagnostics === 'ready'
        ? 'ready'
        : input.diagnostics === 'failed'
          ? 'failed'
          : input.diagnostics === 'queued' || input.diagnostics === 'running'
            ? 'working'
            : 'inactive',
    explanation:
      input.diagnostics === 'idle'
        ? 'No legacy-only token trace is running.'
        : 'This diagnostic is separate from the receipt and session chat path.',
    action:
      input.diagnostics === 'failed' ? 'Open Settings diagnostics for the retained error.' : null
  }

  return [
    backend,
    discoveryStage(input.network),
    rpcStage(generator),
    canary,
    generatorStage,
    generation,
    websocket,
    diagnostics
  ]
}
