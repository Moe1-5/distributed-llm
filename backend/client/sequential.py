"""
sequential.py
Client-side routing — discovers nodes via DHT and chains
forward passes through them in layer order.
 
RPC UID lookup:
    Each node stores its rpc_uid in the DHT node_info entry.
    sequential.py reads this uid and calls get_experts() with it.
    This decouples the UID format from the peer_id.
 
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HIVEMIND IMPORT NOTES (v1.1.12)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
hivemind.dht only exports: DHT, DHTNode, DHTID, DHTValue
get_dht_time does NOT live in hivemind.dht — that was the
original silent-crash root cause.
 
  from hivemind.utils import get_dht_time   ✅  correct (hivemind/utils/)
  from hivemind import get_dht_time         ✅  also fine (re-exported)
  from hivemind.dht import get_dht_time     ❌  ImportError in 1.1.12
 
If _announce() on the server side uses the ❌ form, it will
raise ImportError at call time, get swallowed by a bare
except-and-log-warning block, and the DHT will stay empty.
That is why _discover_nodes() returns [] even when the node
appears to be running.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
 
import sys
import time
import traceback
from threading import Event
from typing import Optional
from uuid import uuid4
 
import hivemind
import torch
from hivemind.moe import get_experts
from hivemind.moe.client.remote_expert_worker import RemoteExpertWorker
from hivemind.proto import runtime_pb2
from hivemind.utils.logging import get_logger

from client.coverage import (
    plan_health_aware_routes,
    provider_identity,
    route_requirement_ranges,
    select_route,
)
from client.failover import (
    RouteAttemptError,
    RouteCancellationError,
    RouteFailoverConfig,
    get_route_failover_config,
)
from client.health import (
    ProviderHealthConfig,
    ProviderHealthMonitor,
    ProviderHealthRegistry,
    ProviderKey,
    get_provider_health_config,
)
from client.rpc_policy import (
    RPCAttemptPolicy,
    classify_rpc_error,
    get_rpc_attempt_policy,
    is_retryable_rpc_error,
    is_safe_receipt_fallback,
)
from incentives.protocol import decode_metadata_tensor, encode_metadata_tensor
from incentives.receipts import (
    accept_worker_receipt,
    build_submission,
    create_inference_request,
    verify_presence,
)
from incentives.runtime import UsefulWorkRuntime, get_useful_work_runtime
 
logger = get_logger(__name__)
 
def shutdown_remote_expert_p2p(dht: object) -> bool:
    """Close Hivemind's cached replicated P2P control client before DHT teardown."""
    replica = getattr(dht, "_p2p_replica", None)
    if replica is None:
        return True
    try:
        RemoteExpertWorker.run_coroutine(replica.shutdown())
        return True
    except Exception as exc:
        logger.warning("Remote expert P2P cleanup failed: %s", exc, exc_info=True)
        return False
    finally:
        # Hivemind 1.1.12 caches this wrapper but DHT.shutdown() does not close it.
        # Clear the private cache so later lifecycle code cannot reuse a dead client.
        try:
            setattr(dht, "_p2p_replica", None)
        except Exception:
            logger.warning("Could not clear the cached remote expert P2P wrapper")
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Module-level import self-check
# Runs once when the file is imported. Any bad import on the SERVER side would
# produce exactly this kind of silent failure, so we demonstrate loudly what it
# looks like and how to detect it.
# ─────────────────────────────────────────────────────────────────────────────
 
def _verify_imports() -> None:
    """
    Validate that every hivemind symbol this module depends on is actually
    importable. Logs a specific, actionable error for each missing symbol
    instead of letting Python raise a generic ImportError at call-time.
 
    This is the pattern that was MISSING on the server side:
      - server imported from hivemind.dht (wrong path)
      - ImportError was caught silently → _announce() never ran → DHT empty
    """
    required = {
        "hivemind.moe":          ["get_experts"],
        "hivemind.utils":        ["get_dht_time"],          # NOT hivemind.dht
        "hivemind.utils.logging": ["get_logger"],
    }
 
    all_ok = True
    for module_path, symbols in required.items():
        try:
            mod = __import__(module_path, fromlist=symbols)
        except ImportError as e:
            logger.critical(
                f"[import-check] FAILED to import module '{module_path}': {e}\n"
                f"  → full traceback:\n{traceback.format_exc()}"
            )
            all_ok = False
            continue
 
        for sym in symbols:
            if not hasattr(mod, sym):
                logger.critical(
                    f"[import-check] Module '{module_path}' exists but has no "
                    f"attribute '{sym}'. This symbol was moved or renamed in your "
                    f"installed hivemind version ({_hivemind_version()}). "
                    f"Available attributes: {[a for a in dir(mod) if not a.startswith('_')]}"
                )
                all_ok = False
            else:
                logger.debug(
                    f"[import-check] OK  {module_path}.{sym}"
                )
 
    if not all_ok:
        logger.critical(
            "[import-check] One or more required imports are broken. "
            "sequential.py will crash at runtime when those code paths are hit. "
            "Fix the imports before starting the server."
        )
    else:
        logger.debug("[import-check] All imports verified OK.")
 
 
def _hivemind_version() -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version("hivemind")
    except Exception:
        return "unknown"
 
 
_verify_imports()
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────────────────────────────────────
 
def _shape_str(t: torch.Tensor) -> str:
    """'[1, 32, 768] torch.float32' — compact tensor description for log lines."""
    return f"{list(t.shape)} {t.dtype}"
 
 
def _peer_short(peer_id: str) -> str:
    """First 8 chars + ellipsis, keeps log lines readable."""
    return peer_id[:8] + "…"
 
 
def _exc_summary(e: Exception) -> str:
    """One-line exception summary: 'ImportError: No module named hivemind.dht'"""
    return f"{type(e).__name__}: {e}"

class RemoteSequential:
    def __init__(
        self,
        dht:        hivemind.DHT,
        dht_prefix: str,
        num_layers: int,
        model_name: Optional[str] = None,
        useful_work_runtime: Optional[UsefulWorkRuntime] = None,
        rpc_attempt_policy: Optional[RPCAttemptPolicy] = None,
        health_config: Optional[ProviderHealthConfig] = None,
        failover_config: Optional[RouteFailoverConfig] = None,
    ):
        if dht is None:
            raise ValueError("dht must not be None")
        if num_layers < 0:
            raise ValueError(f"num_layers must be >= 0, got {num_layers}")
        if model_name is not None and not model_name.strip():
            raise ValueError("model_name must not be empty")

        self.dht        = dht
        self.dht_prefix = dht_prefix
        self.num_layers = num_layers
        self.model_name = model_name
        self.useful_work_runtime = useful_work_runtime or get_useful_work_runtime()
        self.rpc_attempt_policy = rpc_attempt_policy or get_rpc_attempt_policy()
        self.health_config = health_config or get_provider_health_config()
        self.failover_config = failover_config or get_route_failover_config()
        self.health_registry = ProviderHealthRegistry(self.health_config)
        self.health_monitor: Optional[ProviderHealthMonitor] = None
        self._replica_cursors: dict[tuple[int, int], int] = {}
        self._last_forward_metrics: dict = {}
        self._session_id: Optional[str] = None
        self._session_route: Optional[list[dict]] = None
        self._session_snapshot_revision: Optional[tuple[str, str]] = None
        self._session_nodes: Optional[list[dict]] = None
        self._route_quarantine: dict[tuple[str, str, int, int], float] = {}
        self._last_failover: dict = {
            "attempt_count": 0,
            "failed_over": False,
            "reasons": [],
        }

    def start_health_monitor(self) -> None:
        if self.health_monitor is not None and self.health_monitor.running:
            return
        self.health_monitor = ProviderHealthMonitor(
            config=self.health_config,
            registry=self.health_registry,
            discover=self._scan_node_metadata,
            classify=self._classify_health_roles,
            probe=self._probe_provider,
        )
        self.health_monitor.start()

    def stop_health_monitor(self, timeout: float = 2.0) -> bool:
        monitor = self.health_monitor
        if monitor is None:
            return True
        stopped = monitor.stop(timeout=timeout)
        if not stopped:
            logger.warning("Provider health monitor did not stop all active probes in time")
        self.health_monitor = None
        return stopped

    def _classify_health_roles(self, nodes: list[dict]) -> tuple[list[dict], list[dict]]:
        serving_nodes = [node for node in nodes if self._is_serving_node(node)]
        plan = plan_health_aware_routes(
            serving_nodes,
            self.num_layers,
            self.health_registry.snapshot(),
            max_alternates=self.failover_config.max_alternates,
            allow_degraded=self.failover_config.allow_degraded,
        )
        if plan["active"] is not None:
            selected = plan["active"]["route"]
            selected_keys = {provider_identity(node) for node in selected}
            standby = [
                node
                for node in serving_nodes
                if provider_identity(node) not in selected_keys
            ]
            return selected, standby
        return select_route(serving_nodes, self.num_layers)

    def _health_route_plan(self, nodes: list[dict]) -> dict:
        monitor = self.health_monitor
        health_snapshot = (
            self.health_registry.snapshot()
            if monitor is not None and monitor.running
            else None
        )
        return plan_health_aware_routes(
            [node for node in nodes if self._is_serving_node(node)],
            self.num_layers,
            health_snapshot,
            max_alternates=self.failover_config.max_alternates,
            allow_degraded=self.failover_config.allow_degraded,
        )

    def _quarantine_active(self, node: dict, *, now: float) -> bool:
        key = provider_identity(node)
        failed_at = self._route_quarantine.get(key)
        if failed_at is None:
            return False
        health = self.health_registry.get(ProviderKey.from_node(node))
        last_success = health.get("last_success_at") if health else None
        if isinstance(last_success, (int, float)) and last_success > failed_at:
            self._route_quarantine.pop(key, None)
            return False
        if now - failed_at >= self.failover_config.quarantine_seconds:
            self._route_quarantine.pop(key, None)
            return False
        return True

    def _eligible_attempt_routes(self, plan: dict) -> list[dict]:
        candidates = [
            candidate
            for candidate in [plan.get("active"), *plan.get("alternates", [])]
            if candidate is not None
        ]
        now = time.time()
        eligible = [
            candidate
            for candidate in candidates
            if not any(self._quarantine_active(node, now=now) for node in candidate["route"])
        ]
        if (
            self._session_route is not None
            and self._session_snapshot_revision
            == (plan.get("coverage_revision"), plan.get("health_revision"))
            and not any(self._quarantine_active(node, now=now) for node in self._session_route)
        ):
            session_keys = tuple(provider_identity(node) for node in self._session_route)
            for index, candidate in enumerate(eligible):
                if tuple(provider_identity(node) for node in candidate["route"]) == session_keys:
                    eligible.insert(0, eligible.pop(index))
                    break
        return eligible

    def _probe_provider(self, node: dict) -> None:
        deadline = time.monotonic() + self.health_config.probe_timeout_seconds
        lookup = get_experts(
            self.dht,
            [str(node["rpc_uid"])],
            return_future=True,
        )
        try:
            experts = lookup.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:
            lookup.cancel()
            raise
        expert = experts[0] if experts else None
        if expert is None:
            raise RuntimeError(f"Expert {node['rpc_uid']} was not found")
        rpc_info = RemoteExpertWorker.run_coroutine(
            expert.stub.rpc_info(runtime_pb2.ExpertUID(uid=expert.uid)),
            return_future=True,
        )
        try:
            rpc_info.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:
            rpc_info.cancel()
            raise

    def get_health_readiness(self) -> dict:
        monitor = self.health_monitor
        if monitor is None or not monitor.running:
            return {
                "enabled": False,
                "route_ready": False,
                "reasons": ["Provider health monitor is not running."],
                "selected_route": [],
                "standby_route": [],
                **self.health_registry.snapshot(),
            }

        nodes = monitor.latest_nodes()
        plan = self._health_route_plan(nodes)
        selected = plan["active"]["route"] if plan["active"] is not None else []
        alternate_routes = [candidate["route"] for candidate in plan["alternates"]]
        selected_keys = {provider_identity(node) for node in selected}
        alternate_keys = {
            provider_identity(node)
            for route in alternate_routes
            for node in route
        } - selected_keys
        standby = [
            node
            for node in nodes
            if provider_identity(node) not in selected_keys | alternate_keys
        ]
        health = monitor.snapshot()
        health_by_key = {
            ProviderKey.from_node(provider): provider
            for provider in health["providers"]
            if provider.get("rpc_uid") != "invalid"
        }
        reasons: list[str] = []
        if not selected:
            requirements = plan["unavailable_ranges"]
            formatted = ", ".join(
                f"{item['start']}-{item['end']}" for item in requirements
            ) or "a complete adjacent provider route"
            reasons.append(f"No healthy complete route; unavailable layers {formatted}.")

        selected_health: list[dict] = []
        for node in selected:
            provider = health_by_key.get(ProviderKey.from_node(node))
            if provider is None:
                provider = {
                    **ProviderKey.from_node(node).__dict__,
                    "state": "checking",
                    "reason": "Awaiting first RPC health probe.",
                    "dht_present": True,
                    "protocol_compatible": True,
                    "role": "selected",
                }
            selected_health.append(provider)
            if provider.get("state") not in (
                {"healthy", "degraded"}
                if self.failover_config.allow_degraded
                else {"healthy"}
            ):
                reasons.append(
                    f"Provider {str(node['peer_id'])[:8]} is "
                    f"{provider.get('state', 'checking')}: "
                    f"{provider.get('reason') or 'RPC health is not current.'}"
                )

        alternate_health = [
            health_by_key.get(ProviderKey.from_node(node), {
                **ProviderKey.from_node(node).__dict__,
                "state": "checking",
                "reason": "Awaiting first RPC health probe.",
                "role": "standby",
            })
            for route in alternate_routes
            for node in route
            if provider_identity(node) in alternate_keys
        ]
        standby_health = [
            health_by_key.get(ProviderKey.from_node(node), {
                **ProviderKey.from_node(node).__dict__,
                "state": "checking",
                "reason": "Awaiting first RPC health probe.",
                "role": "standby",
            })
            for node in standby
        ]
        provider_roles = []
        for provider in health["providers"]:
            identity = provider_identity(provider)
            provider_roles.append(
                {
                    **provider,
                    "route_role": (
                        "active"
                        if identity in selected_keys
                        else "alternate"
                        if identity in alternate_keys
                        else "standby"
                    ),
                }
            )
        return {
            **health,
            "providers": provider_roles,
            "enabled": True,
            "route_ready": bool(selected) and not reasons,
            "reasons": reasons,
            "warnings": (
                ["Active route contains a degraded provider."]
                if plan["active"] is not None and plan["active"]["degraded"]
                else []
            ),
            "route_revision": plan["route_revision"],
            "coverage_revision": plan["coverage_revision"],
            "selected_route": selected,
            "active_route": plan["active"],
            "alternate_routes": plan["alternates"],
            "standby_route": standby,
            "selected_providers": selected_health,
            "alternate_providers": alternate_health,
            "standby_providers": standby_health,
            "last_failover": dict(self._last_failover),
        }

    def _assert_route_health(self, route: list[dict]) -> None:
        monitor = self.health_monitor
        if monitor is None or not monitor.running:
            return
        failures: list[str] = []
        for node in route:
            health = self.health_registry.get(ProviderKey.from_node(node))
            eligible_states = (
                {"healthy", "degraded"}
                if self.failover_config.allow_degraded
                else {"healthy"}
            )
            if health is None or health.get("state") not in eligible_states:
                state = health.get("state", "checking") if health else "checking"
                reason = health.get("reason") if health else "No health record yet."
                failures.append(f"{str(node['peer_id'])[:8]}={state}: {reason}")
        if failures:
            raise RuntimeError("Selected route health is not ready: " + "; ".join(failures))

    def _validate_node_metadata(self, info: dict, peer_id: str = "unknown") -> dict:
        required = {"peer_id", "layer_start", "layer_end", "model_name", "rpc_uid"}
        missing = required - set(info.keys())
        if missing:
            raise ValueError(f"Node {peer_id[:8]} missing fields: {sorted(missing)}")

        metadata_peer_id = str(info["peer_id"])
        model_name = str(info["model_name"]).strip()
        if not model_name:
            raise ValueError(f"Node {metadata_peer_id[:8]} has empty model_name")
        if self.model_name is not None and model_name != self.model_name:
            raise ValueError(
                f"Node {metadata_peer_id[:8]} model_name {model_name!r} "
                f"does not match generator model {self.model_name!r}"
            )

        try:
            layer_start = int(info["layer_start"])
            layer_end = int(info["layer_end"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Node {metadata_peer_id[:8]} has non-integer layer range: "
                f"{info.get('layer_start')!r}-{info.get('layer_end')!r}"
            ) from exc

        if layer_start < 0:
            raise ValueError(
                f"Node {metadata_peer_id[:8]} has negative layer_start: {layer_start}"
            )
        if layer_end <= layer_start:
            raise ValueError(
                f"Node {metadata_peer_id[:8]} has invalid layer range: "
                f"{layer_start}-{layer_end}"
            )
        if self.num_layers > 0 and layer_end > self.num_layers:
            raise ValueError(
                f"Node {metadata_peer_id[:8]} wants layers {layer_start}-{layer_end}, "
                f"but model has {self.num_layers} layers"
            )

        rpc_uid = str(info["rpc_uid"]).strip()
        if not rpc_uid:
            raise ValueError(f"Node {metadata_peer_id[:8]} has empty rpc_uid")

        layers_loaded = bool(info.get("layers_loaded", True))
        rpc_running = bool(info.get("rpc_running", True))
        running = bool(info.get("running", layers_loaded and rpc_running))

        receipt_capability: dict = {}
        receipt_fields = {
            "receipt_protocol_version",
            "receipt_rpc_uid",
            "application_public_key",
            "application_presence",
            "model_revision",
        }
        if any(field in info for field in receipt_fields):
            try:
                missing_receipt_fields = receipt_fields - set(info)
                if missing_receipt_fields:
                    raise ValueError(
                        f"missing receipt fields: {sorted(missing_receipt_fields)}"
                    )
                if int(info["receipt_protocol_version"]) != 1:
                    raise ValueError("unsupported receipt protocol version")
                receipt_rpc_uid = str(info["receipt_rpc_uid"]).strip()
                application_public_key = str(info["application_public_key"]).strip()
                model_revision = str(info["model_revision"]).strip()
                if not receipt_rpc_uid or not application_public_key or not model_revision:
                    raise ValueError("empty receipt capability field")
                verify_presence(
                    info["application_presence"],
                    public_key=application_public_key,
                    peer_id=metadata_peer_id,
                )
                receipt_capability = {
                    "receipt_protocol_version": 1,
                    "receipt_rpc_uid": receipt_rpc_uid,
                    "application_public_key": application_public_key,
                    "application_presence": info["application_presence"],
                    "model_revision": model_revision,
                }
            except Exception as exc:
                logger.warning(
                    "Ignoring invalid receipt capability from %s: %s",
                    metadata_peer_id[:8],
                    exc,
                )

        sanitized_info = {
            key: value for key, value in info.items() if key not in receipt_fields
        }
        return {
            **sanitized_info,
            "peer_id": metadata_peer_id,
            "model_name": model_name,
            "layer_start": layer_start,
            "layer_end": layer_end,
            "rpc_uid": rpc_uid,
            "device": str(info.get("device", "unknown")),
            "maddrs": info.get("maddrs", []),
            "layers_loaded": layers_loaded,
            "rpc_running": rpc_running,
            "running": running,
            **receipt_capability,
        }

    def start_session(self, session_id: Optional[str] = None) -> str:
        self._session_id = session_id or str(uuid4())
        self._session_route = None
        self._session_snapshot_revision = None
        self._session_nodes = None
        self._route_quarantine.clear()
        return self._session_id

    def end_session(self) -> None:
        self._session_id = None
        self._session_route = None
        self._session_snapshot_revision = None
        self._session_nodes = None
        self._route_quarantine.clear()

    def _receipt_route(self, ordered_nodes: list[dict]) -> Optional[list[dict]]:
        runtime = self.useful_work_runtime
        if (
            not runtime.enabled
            or runtime.identity is None
            or self._session_id is None
            or self.model_name is None
        ):
            return None
        peer_id = getattr(self.dht, "peer_id", None)
        if peer_id is None:
            return None
        generator_peer_id = str(peer_id)
        runtime.bind_peer_id(generator_peer_id)
        route = []
        revisions = {str(node.get("model_revision", "")) for node in ordered_nodes}
        if len(revisions) != 1:
            logger.warning("Receipt accounting requires one model revision across the route")
            return None
        for node in ordered_nodes:
            if not all(
                node.get(field)
                for field in (
                    "receipt_rpc_uid",
                    "application_public_key",
                    "application_presence",
                    "model_revision",
                )
            ):
                return None
            route.append(
                {
                    "peer_id": node["peer_id"],
                    "application_public_key": node["application_public_key"],
                    "rpc_uid": node["receipt_rpc_uid"],
                    "layer_start": node["layer_start"],
                    "layer_end": node["layer_end"],
                }
            )
        return route

    def _is_serving_node(self, node: dict) -> bool:
        return (
            bool(node.get("running", False))
            and bool(node.get("layers_loaded", False))
            and bool(node.get("rpc_running", False))
            and bool(str(node.get("rpc_uid", "")).strip())
        )

    def _plan_route(self, nodes: list[dict]) -> list[dict]:
        """Select the best complete adjacent route and rotate exact replicas."""
        if not nodes:
            raise ValueError("No nodes available for routing")
        ordered_nodes, _ = select_route(
            nodes,
            self.num_layers,
            self._replica_cursors,
            advance_replicas=True,
        )
        if not ordered_nodes:
            requirements = route_requirement_ranges(nodes, self.num_layers)
            formatted = ", ".join(
                f"{item['start']}-{item['end']}" for item in requirements
            ) or "an exactly adjacent provider range"
            raise ValueError(f"No complete adjacent route; needs layers {formatted}")
        return ordered_nodes

    def _build_route_plan(self, discovered_nodes: list[dict]) -> dict:
        if not discovered_nodes:
            raise RuntimeError(
                "No nodes found on the DHT. Make sure at least one node is running."
            )
        validated_nodes = [
            self._validate_node_metadata(node, str(node.get("peer_id", "unknown")))
            for node in discovered_nodes
        ]
        serving_nodes = [node for node in validated_nodes if self._is_serving_node(node)]
        if not serving_nodes:
            raise RuntimeError(
                "No serving nodes found on the DHT. Make sure at least one node is online."
            )
        coverage = self._check_coverage(serving_nodes)
        if not coverage["complete"]:
            requirements = route_requirement_ranges(serving_nodes, self.num_layers)
            formatted = ", ".join(
                f"{item['start']}-{item['end']}" for item in requirements
            )
            raise RuntimeError(f"Incomplete layer coverage - needs layers {formatted}")
        plan = self._health_route_plan(serving_nodes)
        if plan["active"] is None:
            formatted = ", ".join(
                f"{item['start']}-{item['end']}"
                for item in plan["unavailable_ranges"]
            ) or "a complete healthy provider chain"
            raise RuntimeError(
                f"No healthy complete route; unavailable layers {formatted}"
            )
        return plan

    def validate_route(self, nodes: Optional[list[dict]] = None) -> list[dict]:
        """Validate discovered nodes before a forward pass and return a safe route plan."""
        discovered_nodes = list(nodes) if nodes is not None else self._discover_nodes()
        plan = self._build_route_plan(discovered_nodes)
        ordered_nodes = plan["active"]["route"]
        route_str = " -> ".join(
            f"layers {n['layer_start']}–{n['layer_end']} @ {_peer_short(n['peer_id'])}"
            for n in ordered_nodes
        )
        logger.info("[route] validated route: %s", route_str)
        return ordered_nodes

    def _execute_route_attempt(
        self,
        *,
        route: list[dict],
        initial_hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor],
        position_ids: Optional[torch.Tensor],
        request_id: str,
        input_bytes: int,
        cancel_event: Optional[Event],
    ) -> tuple[torch.Tensor, list[str], list[dict], list[dict]]:
        hidden_states = initial_hidden_states
        receipt_route = self._receipt_route(route)
        node_trace: list[str] = []
        hop_metrics: list[dict] = []
        pending_receipts: list[dict] = []
        for hop_idx, node_info in enumerate(route):
            self._raise_if_cancelled(cancel_event)
            peer_id = node_info["peer_id"]
            layer_start = node_info["layer_start"]
            layer_end = node_info["layer_end"]
            rpc_uid = node_info.get("rpc_uid")
            if not rpc_uid:
                raise RuntimeError(
                    f"Node {peer_id[:8]} has no rpc_uid in DHT metadata. "
                    "Node may not have started its RPC server correctly."
                )
            t_hop = time.perf_counter()
            hidden_states = self._call_node(
                rpc_uid=rpc_uid,
                peer_id=peer_id,
                hidden_states=hidden_states,
                attention_mask=attention_mask,
                position_ids=position_ids,
                node_info=node_info,
                receipt_route=receipt_route,
                pending_receipts=pending_receipts,
                request_id=request_id,
                hop_index=hop_idx,
                cancel_event=cancel_event,
            )
            hop_ms = (time.perf_counter() - t_hop) * 1000
            hop_metrics.append(
                {
                    "peer_id": str(peer_id),
                    "rpc_uid": str(rpc_uid),
                    "layer_start": int(layer_start),
                    "layer_end": int(layer_end),
                    "latency_ms": hop_ms,
                    "receipt_requested": receipt_route is not None,
                    "input_bytes": input_bytes,
                    "output_bytes": hidden_states.numel() * hidden_states.element_size(),
                }
            )
            node_trace.append(f"{peer_id[:8]}… (layers {layer_start}→{layer_end})")
        return hidden_states, node_trace, hop_metrics, pending_receipts

    @staticmethod
    def _raise_if_cancelled(cancel_event: Optional[Event]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RouteCancellationError(
                "Generation was cancelled; no additional route work was started."
            )

    @staticmethod
    def _wait_backoff(seconds: float, cancel_event: Optional[Event]) -> None:
        if seconds <= 0:
            return
        if cancel_event is not None:
            if cancel_event.wait(seconds):
                raise RouteCancellationError(
                    "Generation was cancelled during retry backoff."
                )
            return
        time.sleep(seconds)

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

    def forward(
        self,
        hidden_states:  torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids:   Optional[torch.Tensor] = None,
        cancel_event: Optional[Event] = None,
    ) -> tuple[torch.Tensor, list[str]]:
        if hidden_states.dim() != 3:
            raise ValueError(
                f"hidden_states must be [batch, seq_len, hidden_size], got {hidden_states.shape}"
            )
 
        self._raise_if_cancelled(cancel_event)
        t_start = time.perf_counter()
        input_bytes = sum(
            tensor.numel() * tensor.element_size()
            for tensor in (hidden_states, attention_mask, position_ids)
            if tensor is not None
        )
        logger.info(
            f"[forward] ── BEGIN FORWARD PASS ──────────────────────────\n"
            f"  hidden_states : {_shape_str(hidden_states)}\n"
            f"  attention_mask: {'provided' if attention_mask is not None else 'None'}\n"
            f"  position_ids  : {'provided' if position_ids is not None else 'None'}"
        )
 
        discovery_ms = 0.0
        discovery_started_at = time.perf_counter()
        monitor = self.health_monitor
        if monitor is not None and monitor.running:
            nodes = monitor.latest_nodes()
        elif self._session_nodes is not None:
            nodes = [dict(node) for node in self._session_nodes]
        else:
            nodes = self._discover_nodes()
        discovery_ms = (time.perf_counter() - discovery_started_at) * 1000
        route_started_at = time.perf_counter()
        plan = self._build_route_plan(nodes)
        routes = self._eligible_attempt_routes(plan)
        route_validation_ms = (time.perf_counter() - route_started_at) * 1000
        if not routes:
            raise RuntimeError(
                "No healthy complete route remains after temporary provider quarantine."
            )

        initial_hidden_states = hidden_states
        failed_attempts: list[dict] = []
        accepted_route: list[dict] | None = None
        node_trace: list[str] = []
        hop_metrics: list[dict] = []
        attempt_count = 0
        route_reused = False
        candidate_index = 0
        while (
            attempt_count < self.failover_config.max_attempts
            and candidate_index < len(routes)
        ):
            self._raise_if_cancelled(cancel_event)
            candidate = routes[candidate_index]
            candidate_index += 1
            route = [dict(node) for node in candidate["route"]]
            now = time.time()
            if any(self._quarantine_active(node, now=now) for node in route):
                continue
            attempt_count += 1
            request_id = str(uuid4())
            route_reused = (
                attempt_count == 1
                and self._session_snapshot_revision
                == (plan["coverage_revision"], plan["health_revision"])
                and self._session_route is not None
                and tuple(provider_identity(node) for node in route)
                == tuple(provider_identity(node) for node in self._session_route)
            )
            self._assert_route_health(route)
            try:
                hidden_states, node_trace, hop_metrics, pending_receipts = (
                    self._execute_route_attempt(
                        route=route,
                        initial_hidden_states=initial_hidden_states,
                        attention_mask=attention_mask,
                        position_ids=position_ids,
                        request_id=request_id,
                        input_bytes=input_bytes,
                        cancel_event=cancel_event,
                    )
                )
                self._raise_if_cancelled(cancel_event)
            except RouteCancellationError:
                self._last_failover = {
                    "attempt_count": attempt_count,
                    "failed_over": False,
                    "reasons": failed_attempts,
                    "cancelled": True,
                }
                raise
            except RouteAttemptError as exc:
                failed_at = time.time()
                self._route_quarantine[provider_identity(exc.node)] = failed_at
                failed_attempts.append(
                    {
                        "attempt": attempt_count,
                        "request_id": request_id,
                        "failure_class": exc.failure_class,
                        "peer_id": str(exc.node.get("peer_id", "")),
                        "layer_start": int(exc.node.get("layer_start", 0)),
                        "layer_end": int(exc.node.get("layer_end", 0)),
                        "reason": str(exc),
                    }
                )
                if exc.failure_class != "pre_execution_transport":
                    self._last_failover = {
                        "attempt_count": attempt_count,
                        "failed_over": False,
                        "reasons": failed_attempts,
                    }
                    raise RuntimeError(
                        f"Route attempt failed with uncertain execution at layers "
                        f"{exc.node.get('layer_start')}-{exc.node.get('layer_end')}; "
                        "automatic failover was suppressed."
                    ) from exc
                if attempt_count >= self.failover_config.max_attempts:
                    break
                backoff = self.failover_config.backoff_for_attempt(attempt_count)
                self._wait_backoff(backoff, cancel_event)
                continue
            accepted_route = route
            for submission in pending_receipts:
                self.useful_work_runtime.submit(submission)
            break

        if accepted_route is None:
            failed = failed_attempts[-1] if failed_attempts else {}
            start = failed.get("layer_start", "unknown")
            end = failed.get("layer_end", "unknown")
            self._last_failover = {
                "attempt_count": attempt_count,
                "failed_over": False,
                "reasons": failed_attempts,
            }
            raise RuntimeError(
                f"Route failed at layers {start}-{end}; no healthy complete alternate "
                f"succeeded within {self.failover_config.max_attempts} attempt(s)."
            )

        if self._session_id is not None:
            self._session_route = [dict(node) for node in accepted_route]
            self._session_snapshot_revision = (
                plan["coverage_revision"],
                plan["health_revision"],
            )
            self._session_nodes = [dict(node) for node in nodes]
        self._last_failover = {
            "attempt_count": attempt_count,
            "failed_over": bool(failed_attempts),
            "reasons": failed_attempts,
        }
 
        total_ms = (time.perf_counter() - t_start) * 1000
        self._last_forward_metrics = {
            "discovery_ms": discovery_ms,
            "route_validation_ms": route_validation_ms,
            "route_reused": route_reused,
            "request_id": request_id,
            "input_bytes": input_bytes,
            "rpc_total_ms": sum(hop["latency_ms"] for hop in hop_metrics),
            "total_ms": total_ms,
            "hops": hop_metrics,
            "attempt_count": attempt_count,
            "failed_over": bool(failed_attempts),
            "failover_reasons": failed_attempts,
            "route_revision": plan["route_revision"],
            "coverage_revision": plan["coverage_revision"],
            "health_revision": plan["health_revision"],
            "active_route": [dict(node) for node in accepted_route],
            "alternate_routes": [
                [dict(node) for node in candidate["route"]]
                for candidate in plan["alternates"]
            ],
        }
        logger.info(
            f"[forward] ── FORWARD PASS COMPLETE ──────────────────────\n"
            f"  hops    : {len(node_trace)}\n"
            f"  attempts: {attempt_count}\n"
            f"  output  : {_shape_str(hidden_states)}\n"
            f"  elapsed : {total_ms:.1f}ms\n"
            f"  trace   : {' → '.join(node_trace)}"
        )
        return hidden_states, node_trace

    def get_last_forward_metrics(self) -> dict:
        """Return timing evidence for the latest successful forward pass."""
        return {
            **self._last_forward_metrics,
            "hops": [
                dict(hop)
                for hop in self._last_forward_metrics.get("hops", [])
            ],
        }

    # ------------------------------------------------------------------
    # RPC call
    # ------------------------------------------------------------------

    def _call_node(
        self,
        rpc_uid:       str,
        peer_id:       str,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids:   Optional[torch.Tensor] = None,
        node_info: Optional[dict] = None,
        receipt_route: Optional[list[dict]] = None,
        pending_receipts: Optional[list[dict]] = None,
        request_id: Optional[str] = None,
        hop_index: int = 0,
        cancel_event: Optional[Event] = None,
    ) -> torch.Tensor:
        request_id = request_id or str(uuid4())
        policy = self.rpc_attempt_policy
        last_error: Optional[Exception] = None
        attempted = 0
        for attempt in range(policy.max_attempts):
            self._raise_if_cancelled(cancel_event)
            attempted = attempt + 1
            attempt_started_at = time.perf_counter()
            try:
                if node_info is not None and receipt_route is not None:
                    try:
                        response, submission = self._rpc_forward_with_receipt(
                            node_info=node_info,
                            route=receipt_route,
                            hidden_states=hidden_states,
                            attention_mask=attention_mask,
                            position_ids=position_ids,
                        )
                        if pending_receipts is not None:
                            pending_receipts.append(submission)
                        result = response
                    except Exception as exc:
                        if not is_safe_receipt_fallback(exc):
                            raise
                        logger.warning(
                            "request=%s peer=%s receipt capability unavailable; "
                            "using legacy RPC without credit: %s",
                            request_id,
                            peer_id[:8],
                            exc,
                        )
                        result = self._rpc_forward(
                            rpc_uid,
                            peer_id,
                            hidden_states,
                            attention_mask=attention_mask,
                            position_ids=position_ids,
                        )
                else:
                    result = self._rpc_forward(
                        rpc_uid,
                        peer_id,
                        hidden_states,
                        attention_mask=attention_mask,
                        position_ids=position_ids,
                    )
                elapsed_seconds = time.perf_counter() - attempt_started_at
                if elapsed_seconds >= policy.slow_request_warning_seconds:
                    logger.warning(
                        "request=%s hop=%s peer=%s slow expert call elapsed_seconds=%.1f",
                        request_id,
                        hop_index + 1,
                        peer_id[:8],
                        elapsed_seconds,
                    )
                if node_info is not None:
                    self.health_registry.record_success(
                        ProviderKey.from_node(node_info),
                        now=time.time(),
                        latency_ms=elapsed_seconds * 1000,
                    )
                logger.info(
                    "request=%s hop=%s peer=%s rpc_uid=%s attempt=%s/%s "
                    "layers=%s-%s shape=%s bytes=%s elapsed_ms=%.1f status=complete",
                    request_id,
                    hop_index + 1,
                    peer_id[:8],
                    rpc_uid,
                    attempted,
                    policy.max_attempts,
                    node_info.get("layer_start", "unknown") if node_info else "unknown",
                    node_info.get("layer_end", "unknown") if node_info else "unknown",
                    tuple(hidden_states.shape),
                    hidden_states.numel() * hidden_states.element_size(),
                    elapsed_seconds * 1000,
                )
                return result
            except Exception as e:
                if isinstance(e, RouteCancellationError):
                    raise
                last_error = e
                elapsed_ms = (time.perf_counter() - attempt_started_at) * 1000
                failure_class = classify_rpc_error(e)
                retryable = is_retryable_rpc_error(e) and attempted < policy.max_attempts
                logger.warning(
                    "request=%s hop=%s peer=%s rpc_uid=%s attempt=%s/%s elapsed_ms=%.1f "
                    "failure=%s retry=%s error=%s",
                    request_id,
                    hop_index + 1,
                    peer_id[:8],
                    rpc_uid,
                    attempted,
                    policy.max_attempts,
                    elapsed_ms,
                    failure_class,
                    retryable,
                    e,
                )
                if retryable:
                    backoff = policy.backoff_seconds(attempted)
                    self._wait_backoff(backoff, cancel_event)
                else:
                    break
        terminal_message = (
            f"RPC request {request_id} to node {peer_id[:8]} failed after "
            f"{attempted}/{policy.max_attempts} attempt(s) "
            f"({classify_rpc_error(last_error) if last_error else 'unknown'}). "
            f"Last error: {last_error}"
        )
        failure_class = classify_rpc_error(last_error) if last_error else "unknown"
        failure_node = node_info or {
            "peer_id": peer_id,
            "rpc_uid": rpc_uid,
            "layer_start": 0,
            "layer_end": 0,
        }
        terminal_error = RouteAttemptError(
            terminal_message,
            failure_class=failure_class,
            node=failure_node,
            cause=last_error,
        )
        if node_info is not None:
            self.health_registry.record_failure(
                ProviderKey.from_node(node_info),
                now=time.time(),
                reason=str(terminal_error),
            )
        raise terminal_error

    def validate_reachable_route(self) -> list[dict]:
        """Validate coverage and prove that every selected expert can answer RPC metadata."""
        if self.health_monitor is not None and self.health_monitor.running:
            readiness = self.get_health_readiness()
            if not readiness["route_ready"]:
                raise RuntimeError("; ".join(readiness["reasons"]))
            return [dict(node) for node in readiness["selected_route"]]
        route = self.validate_route()
        rpc_uids = [str(node["rpc_uid"]) for node in route]
        experts = get_experts(self.dht, rpc_uids)
        failures: list[str] = []
        for index, node in enumerate(route):
            peer_id = str(node["peer_id"])
            expert = experts[index] if index < len(experts) else None
            if expert is None:
                failures.append(f"{peer_id[:8]}: expert {node['rpc_uid']} not found")
                continue
            try:
                expert.info
            except Exception as exc:
                failures.append(f"{peer_id[:8]}: {exc}")
        if failures:
            raise RuntimeError("Route RPC probe failed: " + "; ".join(failures))
        return route

    def validate_tensor_route(self, hidden_size: int) -> dict:
        """Send one deterministic position through the selected route before readiness."""
        if hidden_size <= 0:
            raise ValueError("hidden_size must be positive")
        route = self.validate_reachable_route()
        started_at = time.perf_counter()
        hidden_states = torch.zeros((1, 1, hidden_size), dtype=torch.float32)
        attention_mask = torch.ones((1, 1), dtype=torch.long)
        position_ids = torch.zeros((1, 1), dtype=torch.long)
        output, node_trace = self.forward(
            hidden_states,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )
        if tuple(output.shape) != tuple(hidden_states.shape):
            raise RuntimeError(
                "Tensor route canary returned an unexpected shape: "
                f"expected {tuple(hidden_states.shape)}, got {tuple(output.shape)}"
            )
        if not bool(torch.isfinite(output).all()):
            raise RuntimeError("Tensor route canary returned non-finite values")
        return {
            "ok": True,
            "elapsed_ms": (time.perf_counter() - started_at) * 1000,
            "route": [dict(node) for node in route],
            "node_trace": list(node_trace),
            "shape": list(output.shape),
        }

    def _rpc_forward(
        self,
        rpc_uid:       str,
        peer_id:       str,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        """Call remote node using its rpc_uid stored in DHT metadata."""
        experts = get_experts(self.dht, [rpc_uid])

        if not experts or experts[0] is None:
            raise RuntimeError(
                f"Node uid={rpc_uid} not found in DHT. Node may have gone offline."
            )

        expert = experts[0]

        # Flatten [batch, seq_len, hidden] → [batch*seq_len, hidden]
        # hivemind experts expect 2D input
        # batch, seq_len, hidden = hidden_states.shape
        # flat = hidden_states.reshape(batch * seq_len, hidden)

        output = expert.forward(
            hidden_states,
            attention_mask=attention_mask,
            position_ids=position_ids
            )
        if isinstance(output, tuple):
            output = output[0]

        if output is None:
            raise RuntimeError(f"Node {rpc_uid} returned None")
        return output

    def _rpc_forward_with_receipt(
        self,
        *,
        node_info: dict,
        route: list[dict],
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor],
        position_ids: Optional[torch.Tensor],
    ) -> torch.Tensor:
        runtime = self.useful_work_runtime
        if runtime.identity is None or self._session_id is None:
            raise RuntimeError("Useful-work session is not initialized")
        if hidden_states.shape[0] != 1:
            raise RuntimeError("Receipt protocol v1 currently requires batch size one")
        generator_peer_id = str(getattr(self.dht, "peer_id", ""))
        worker = next(
            member
            for member in route
            if member["peer_id"] == node_info["peer_id"]
            and member["layer_start"] == node_info["layer_start"]
            and member["layer_end"] == node_info["layer_end"]
        )
        request_document = create_inference_request(
            runtime.identity,
            generator_peer_id=generator_peer_id,
            session_id=self._session_id,
            model_name=str(self.model_name),
            model_revision=str(node_info["model_revision"]),
            route=route,
            worker=worker,
            hidden_states=hidden_states,
            position_count=int(hidden_states.shape[0] * hidden_states.shape[1]),
        )
        experts = get_experts(self.dht, [worker["rpc_uid"]])
        if not experts or experts[0] is None:
            raise RuntimeError(f"Receipt expert {worker['rpc_uid']} was not found")
        output = experts[0].forward(
            hidden_states,
            encode_metadata_tensor(request_document),
            attention_mask=attention_mask,
            position_ids=position_ids,
        )
        if not isinstance(output, tuple) or len(output) != 2:
            raise RuntimeError("Receipt expert returned an invalid response")
        response, metadata_tensor = output
        metadata = decode_metadata_tensor(metadata_tensor)
        worker_receipt = metadata.get("worker_receipt")
        worker_presence = metadata.get("worker_presence")
        if not isinstance(worker_receipt, dict) or not isinstance(worker_presence, dict):
            raise RuntimeError("Receipt expert omitted its signed receipt or presence")
        verify_presence(
            worker_presence,
            public_key=str(node_info["application_public_key"]),
            peer_id=str(node_info["peer_id"]),
        )
        acceptance = accept_worker_receipt(
            runtime.identity,
            request_document,
            worker_receipt,
            response,
            generator_peer_id=generator_peer_id,
        )
        submission = build_submission(
            worker_receipt=worker_receipt,
            generator_acceptance=acceptance,
            worker_presence=worker_presence,
            generator_presence=runtime.presence(),
            route=route,
        )
        return response, submission

    # ------------------------------------------------------------------
    # DHT discovery
    # ------------------------------------------------------------------

    def _discover_nodes(self) -> list[dict]:
        """Two-level DHT scan: members list → per-node metadata."""
        try:
            nodes, errors = self._scan_node_metadata()
        except Exception as exc:
            logger.warning("DHT members lookup failed: %s", exc)
            return []
        for error in errors:
            logger.warning(
                "Failed to fetch or validate metadata for %s: %s",
                str(error.get("peer_id", "unknown"))[:8],
                error.get("reason", "invalid metadata"),
            )
        logger.info(f"Discovered {len(nodes)} node(s) on '{self.dht_prefix}'")
        return nodes

    def _scan_node_metadata(self) -> tuple[list[dict], list[dict]]:
        """Return validated providers and distinct protocol-advertisement errors."""
        nodes: list[dict] = []
        errors: list[dict] = []
        result = self.dht.get(f"{self.dht_prefix}.members", latest=True)
        if result is None or not isinstance(result.value, list):
            return nodes, errors
        peer_ids: list[str] = result.value

        for peer_id in peer_ids:
            try:
                result = self.dht.get(
                    f"{self.dht_prefix}.node_info.{peer_id}", latest=True
                )
                if result is None or not isinstance(result.value, dict):
                    continue

                info = self._validate_node_metadata(result.value, str(peer_id))
                nodes.append(info)

            except Exception as exc:
                errors.append(
                    {
                        "peer_id": str(peer_id),
                        "model_name": self.model_name or "unknown",
                        "kind": "protocol_incompatible",
                        "reason": str(exc),
                    }
                )
        return nodes, errors

    # ------------------------------------------------------------------
    # Coverage check
    # ------------------------------------------------------------------

    def _check_coverage(self, nodes: list[dict]) -> dict:
        covered: set[int] = set()
        invalid: list[str] = []
        for node in nodes:
            try:
                layer_start = int(node["layer_start"])
                layer_end = int(node["layer_end"])
            except (TypeError, ValueError):
                invalid.append(
                    f"{node.get('peer_id', 'unknown')}:{node.get('layer_start')}-{node.get('layer_end')}"
                )
                continue
            if layer_start < 0 or layer_end <= layer_start:
                invalid.append(
                    f"{node.get('peer_id', 'unknown')}:{layer_start}-{layer_end}"
                )
                continue
            if self.num_layers > 0 and layer_end > self.num_layers:
                invalid.append(
                    f"{node.get('peer_id', 'unknown')}:{layer_start}-{layer_end}"
                )
                continue
            for i in range(layer_start, layer_end):
                covered.add(i)
        all_layers = set(range(self.num_layers))
        missing    = sorted(all_layers - covered)
        return {
            "complete": len(missing) == 0,
            "covered":  sorted(covered),
            "missing":  missing,
            "invalid":  invalid,
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_network_status(self) -> dict:
        nodes    = self._discover_nodes()
        serving_nodes = [node for node in nodes if self._is_serving_node(node)]
        coverage = self._check_coverage(serving_nodes)
        return {
            "nodes":            nodes,
            "total_layers":     self.num_layers,
            "covered_layers":   len(coverage["covered"]),
            "missing_layers":   coverage["missing"],
            "network_complete": coverage["complete"],
        }
