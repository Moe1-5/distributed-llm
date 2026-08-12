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
from typing import Optional
from uuid import uuid4
 
import hivemind
import torch
from hivemind.moe import get_experts
from hivemind.utils.logging import get_logger

from client.coverage import route_requirement_ranges, select_route
from incentives.protocol import decode_metadata_tensor, encode_metadata_tensor
from incentives.receipts import (
    accept_worker_receipt,
    build_submission,
    create_inference_request,
    verify_presence,
)
from incentives.runtime import UsefulWorkRuntime, get_useful_work_runtime
 
logger = get_logger(__name__)
 
REQUEST_TIMEOUT = 30
MAX_RETRIES     = 2          # ← unchanged from original
 
 
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
        self._replica_cursors: dict[tuple[int, int], int] = {}
        self._last_forward_metrics: dict = {}
        self._session_id: Optional[str] = None

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
        return self._session_id

    def end_session(self) -> None:
        self._session_id = None

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

    def validate_route(self, nodes: Optional[list[dict]] = None) -> list[dict]:
        """Validate discovered nodes before a forward pass and return a safe route plan."""
        discovered_nodes = list(nodes) if nodes is not None else self._discover_nodes()
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
            raise RuntimeError(
                f"Incomplete layer coverage - needs layers {formatted}"
            )
        try:
            ordered_nodes = self._plan_route(serving_nodes)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        route_str = " -> ".join(
            f"layers {n['layer_start']}–{n['layer_end']} @ {_peer_short(n['peer_id'])}"
            for n in ordered_nodes
        )
        logger.info("[route] validated route: %s", route_str)
        return ordered_nodes

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

   
    def forward(
        self,
        hidden_states:  torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids:   Optional[torch.Tensor] = None,
    ) -> tuple[torch.Tensor, list[str]]:
        if hidden_states.dim() != 3:
            raise ValueError(
                f"hidden_states must be [batch, seq_len, hidden_size], got {hidden_states.shape}"
            )
 
        t_start = time.perf_counter()
        logger.info(
            f"[forward] ── BEGIN FORWARD PASS ──────────────────────────\n"
            f"  hidden_states : {_shape_str(hidden_states)}\n"
            f"  attention_mask: {'provided' if attention_mask is not None else 'None'}\n"
            f"  position_ids  : {'provided' if position_ids is not None else 'None'}"
        )
 
        # ── Step 1: node discovery ────────────────────────────────────────────
        logger.debug("[forward] Step 1/3 — discovering nodes …")
        discovery_started_at = time.perf_counter()
        nodes = self._discover_nodes()
        discovery_ms = (time.perf_counter() - discovery_started_at) * 1000
 
        if not nodes:
            # This is almost always caused by a silent _announce() crash.
            # Give the caller enough context to diagnose without reading server logs.
            logger.error(
                "[forward] ✗ No nodes found. Likely root causes (in order of frequency):\n"
                "  1. _announce() on the server crashed silently due to a bad import\n"
                "     (e.g. 'from hivemind.dht import get_dht_time' — wrong path in 1.1.12).\n"
                "     The exception was swallowed and the DHT was never written to.\n"
                "  2. Wrong dht_prefix — client and server are using different prefixes.\n"
                "  3. DHT bootstrap peer unreachable — node started but can't join the swarm.\n"
                "  Check server stdout for lines like:\n"
                "    'Failed to update members index: <ImportError or AttributeError>'\n"
                "  Those are the swallowed crashes."
            )
            raise RuntimeError(
                "No nodes found on the DHT. "
                "Make sure at least one node is running."
            )
 
        logger.info(f"[forward] Step 1/3 ✓ — {len(nodes)} node(s) discovered")
 
        # ── Step 2: route validation ────────────────────────────────────────
        logger.debug(
            f"[forward] Step 2/3 — validating route "
            f"(need layers 0…{self.num_layers - 1}) …"
        )
        route_started_at = time.perf_counter()
        ordered_nodes = self.validate_route(nodes)
        receipt_route = self._receipt_route(ordered_nodes)
        route_validation_ms = (time.perf_counter() - route_started_at) * 1000
        logger.info(
            f"[forward] Step 2/3 ✓ — route validated for {len(ordered_nodes)} node(s)"
        )
 
        # ── Step 3: sequential RPC calls ─────────────────────────────────────
        node_trace = []
        hop_metrics: list[dict] = []
        pending_receipts: list[dict] = []
 
        route_str = " → ".join(
            f"layers {n['layer_start']}–{n['layer_end']} @ {_peer_short(n['peer_id'])}"
            for n in ordered_nodes
        )
        logger.debug(f"[forward] Step 3/3 — routing plan: {route_str}")
 
        for hop_idx, node_info in enumerate(ordered_nodes):
            peer_id     = node_info["peer_id"]
            layer_start = node_info["layer_start"]
            layer_end   = node_info["layer_end"]
            rpc_uid     = node_info.get("rpc_uid")
 
            logger.debug(
                f"[forward] Hop {hop_idx + 1}/{len(ordered_nodes)} | "
                f"peer={_peer_short(peer_id)} layers={layer_start}→{layer_end} | "
                f"rpc_uid={rpc_uid!r} | input={_shape_str(hidden_states)}"
            )
 
            # ── ORIGINAL assert, kept exactly as-is ──────────────────────────
            if not rpc_uid:
                raise RuntimeError(
                    f"Node {peer_id[:8]} has no rpc_uid in DHT metadata. "
                    f"Node may not have started its RPC server correctly."
                )
 
            t_hop         = time.perf_counter()
            hidden_states = self._call_node(
                rpc_uid=rpc_uid,
                peer_id=peer_id,
                hidden_states=hidden_states,
                attention_mask=attention_mask,
                position_ids=position_ids,
                node_info=node_info,
                receipt_route=receipt_route,
                pending_receipts=pending_receipts,
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
                }
            )
 
            logger.debug(
                f"[forward] Hop {hop_idx + 1}/{len(ordered_nodes)} ✓ | "
                f"peer={_peer_short(peer_id)} | "
                f"output={_shape_str(hidden_states)} | {hop_ms:.1f}ms"
            )
 
            node_trace.append(f"{peer_id[:8]}… (layers {layer_start}→{layer_end})")

        for submission in pending_receipts:
            self.useful_work_runtime.submit(submission)
 
        total_ms = (time.perf_counter() - t_start) * 1000
        self._last_forward_metrics = {
            "discovery_ms": discovery_ms,
            "route_validation_ms": route_validation_ms,
            "rpc_total_ms": sum(hop["latency_ms"] for hop in hop_metrics),
            "total_ms": total_ms,
            "hops": hop_metrics,
        }
        logger.info(
            f"[forward] ── FORWARD PASS COMPLETE ──────────────────────\n"
            f"  hops    : {len(node_trace)}\n"
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
    ) -> torch.Tensor:
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
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
                        return response
                    except Exception as exc:
                        logger.warning(
                            "Receipt RPC failed for %s; using legacy RPC without credit: %s",
                            peer_id[:8],
                            exc,
                        )
                return self._rpc_forward(
                    rpc_uid, peer_id, hidden_states,
                    attention_mask=attention_mask,
                    position_ids=position_ids
                    )
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Node {peer_id[:8]} attempt {attempt + 1} failed: {e} — retrying..."
                    )
                    time.sleep(1)
        raise RuntimeError(
            f"Node {peer_id[:8]} failed after {MAX_RETRIES + 1} attempts. "
            f"Last error: {last_error}"
        )

    def validate_reachable_route(self) -> list[dict]:
        """Validate coverage and prove that every selected expert can answer RPC metadata."""
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

    def _rpc_forward(
        self,
        rpc_uid:       str,
        peer_id:       str,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None
    ) -> tuple[torch.Tensor, dict]:
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
        nodes = []

        try:
            result = self.dht.get(f"{self.dht_prefix}.members", latest=True)
            if result is None or not isinstance(result.value, list):
                return []
            peer_ids: list[str] = result.value
        except Exception as e:
            logger.warning(f"DHT members lookup failed: {e}")
            return []

        for peer_id in peer_ids:
            try:
                result = self.dht.get(
                    f"{self.dht_prefix}.node_info.{peer_id}", latest=True
                )
                if result is None or not isinstance(result.value, dict):
                    continue

                info = self._validate_node_metadata(result.value, str(peer_id))
                nodes.append(info)

            except Exception as e:
                logger.warning(f"Failed to fetch or validate metadata for {peer_id[:8]}: {e}")

        logger.info(f"Discovered {len(nodes)} node(s) on '{self.dht_prefix}'")
        return nodes

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
