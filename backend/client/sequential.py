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
 
import hivemind
import torch
from hivemind.moe import get_experts
from hivemind.utils.logging import get_logger
 
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
    ):
        assert dht        is not None, "dht must not be None"
        assert num_layers >= 0,        f"num_layers must be >= 0, got {num_layers}"

        self.dht        = dht
        self.dht_prefix = dht_prefix
        self.num_layers = num_layers

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

   
    def forward(
        self,
        hidden_states:  torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids:   Optional[torch.Tensor] = None,
    ) -> tuple[torch.Tensor, list[str]]:
        assert hidden_states.dim() == 3, (
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
        nodes = self._discover_nodes()
 
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
 
        # ── Step 2: coverage check ────────────────────────────────────────────
        logger.debug(
            f"[forward] Step 2/3 — checking coverage "
            f"(need layers 0…{self.num_layers - 1}) …"
        )
        coverage = self._check_coverage(nodes)
 
        if not coverage["complete"]:
            logger.error(
                f"[forward] ✗ Incomplete layer coverage.\n"
                f"  covered : {coverage['covered']}\n"
                f"  missing : {coverage['missing']}\n"
                f"  Either a node is down or its layer range was mis-configured."
            )
            raise RuntimeError(
                f"Incomplete layer coverage — missing: {coverage['missing']}"
            )
 
        logger.info(
            f"[forward] Step 2/3 ✓ — all {self.num_layers} layer(s) covered "
            f"by {len(nodes)} node(s)"
        )
 
        # ── Step 3: sequential RPC calls ─────────────────────────────────────
        ordered_nodes = sorted(nodes, key=lambda n: n["layer_start"])
        node_trace    = []
 
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
            assert rpc_uid, (
                f"Node {peer_id[:8]} has no rpc_uid in DHT metadata. "
                f"Node may not have started its RPC server correctly."
            )
 
            t_hop         = time.perf_counter()
            hidden_states = self._call_node(
                rpc_uid=rpc_uid,
                peer_id=peer_id,
                hidden_states=hidden_states,
            )
            hop_ms = (time.perf_counter() - t_hop) * 1000
 
            logger.debug(
                f"[forward] Hop {hop_idx + 1}/{len(ordered_nodes)} ✓ | "
                f"peer={_peer_short(peer_id)} | "
                f"output={_shape_str(hidden_states)} | {hop_ms:.1f}ms"
            )
 
            node_trace.append(f"{peer_id[:8]}… (layers {layer_start}→{layer_end})")
 
        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            f"[forward] ── FORWARD PASS COMPLETE ──────────────────────\n"
            f"  hops    : {len(node_trace)}\n"
            f"  output  : {_shape_str(hidden_states)}\n"
            f"  elapsed : {total_ms:.1f}ms\n"
            f"  trace   : {' → '.join(node_trace)}"
        )
        return hidden_states, node_trace

    # ------------------------------------------------------------------
    # RPC call
    # ------------------------------------------------------------------

    def _call_node(
        self,
        rpc_uid:       str,
        peer_id:       str,
        hidden_states: torch.Tensor,
    ) -> torch.Tensor:
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._rpc_forward(rpc_uid, peer_id, hidden_states)
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

    def _rpc_forward(
        self,
        rpc_uid:       str,
        peer_id:       str,
        hidden_states: torch.Tensor,
    ) -> torch.Tensor:
        """Call remote node using its rpc_uid stored in DHT metadata."""
        experts = get_experts(self.dht, [rpc_uid])

        assert experts and experts[0] is not None, (
            f"Node uid={rpc_uid} not found in DHT. Node may have gone offline."
        )

        expert = experts[0]

        # Flatten [batch, seq_len, hidden] → [batch*seq_len, hidden]
        # hivemind experts expect 2D input
        # batch, seq_len, hidden = hidden_states.shape
        # flat = hidden_states.reshape(batch * seq_len, hidden)

        output = expert.forward(hidden_states)
        if isinstance(output, tuple):
            output = output[0]

        assert output is not None, f"Node {rpc_uid} returned None"
        return output

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

                info     = result.value
                required = {"peer_id", "layer_start", "layer_end", "model_name", "rpc_uid"}
                missing  = required - set(info.keys())
                if missing:
                    logger.warning(f"Node {peer_id[:8]} missing fields: {missing}")
                    continue

                nodes.append(info)

            except Exception as e:
                logger.warning(f"Failed to fetch metadata for {peer_id[:8]}: {e}")

        logger.info(f"Discovered {len(nodes)} node(s) on '{self.dht_prefix}'")
        return nodes

    # ------------------------------------------------------------------
    # Coverage check
    # ------------------------------------------------------------------

    def _check_coverage(self, nodes: list[dict]) -> dict:
        covered: set[int] = set()
        for node in nodes:
            for i in range(node["layer_start"], node["layer_end"]):
                covered.add(i)
        all_layers = set(range(self.num_layers))
        missing    = sorted(all_layers - covered)
        return {
            "complete": len(missing) == 0,
            "covered":  sorted(covered),
            "missing":  missing,
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_network_status(self) -> dict:
        nodes    = self._discover_nodes()
        coverage = self._check_coverage(nodes)
        return {
            "nodes":            nodes,
            "total_layers":     self.num_layers,
            "covered_layers":   len(coverage["covered"]),
            "missing_layers":   coverage["missing"],
            "network_complete": coverage["complete"],
        }
