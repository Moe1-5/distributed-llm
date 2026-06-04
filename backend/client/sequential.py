"""
sequential.py
Client-side routing — discovers nodes via DHT and chains
forward passes through them in layer order.

RPC UID lookup:
    Each node stores its rpc_uid in the DHT node_info entry.
    sequential.py reads this uid and calls get_experts() with it.
    This decouples the UID format from the peer_id.
"""

import time
from typing import Optional

import hivemind
import torch
from hivemind.moe import get_experts
from hivemind.utils.logging import get_logger

logger = get_logger(__name__)

REQUEST_TIMEOUT = 30
MAX_RETRIES     = 2


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

        nodes = self._discover_nodes()

        if not nodes:
            raise RuntimeError(
                "No nodes found on the DHT. "
                "Make sure at least one node is running."
            )

        coverage = self._check_coverage(nodes)
        if not coverage["complete"]:
            raise RuntimeError(
                f"Incomplete layer coverage — missing: {coverage['missing']}"
            )

        ordered_nodes = sorted(nodes, key=lambda n: n["layer_start"])
        node_trace    = []

        for node_info in ordered_nodes:
            peer_id     = node_info["peer_id"]
            layer_start = node_info["layer_start"]
            layer_end   = node_info["layer_end"]
            rpc_uid     = node_info.get("rpc_uid")

            assert rpc_uid, (
                f"Node {peer_id[:8]} has no rpc_uid in DHT metadata. "
                f"Node may not have started its RPC server correctly."
            )

            logger.debug(f"Routing layers {layer_start}-{layer_end} → uid={rpc_uid}")

            hidden_states = self._call_node(
                rpc_uid=rpc_uid,
                peer_id=peer_id,
                hidden_states=hidden_states,
            )

            node_trace.append(f"{peer_id[:8]}… (layers {layer_start}→{layer_end})")

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
        batch, seq_len, hidden = hidden_states.shape
        flat = hidden_states.reshape(batch * seq_len, hidden)

        output_flat = expert.forward(flat)

        assert output_flat is not None, f"Node {rpc_uid} returned None"

        # Restore to [batch, seq_len, hidden]
        return output_flat.reshape(batch, seq_len, hidden)

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
