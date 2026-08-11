"""
constants.py
Single source of truth for DistribLLM network configuration.

Bootstrap peers:
    These are the well-known stable entry points for the swarm.
    Configure them through DISTRIBLLM_INITIAL_PEERS. Production participants
    use the project VPS public address; local tests may use a loopback address.

use_ipfs=False:
    Hivemind's P2P layer has a use_ipfs flag. When True, it connects
    to the public IPFS/Petals network — meaning your nodes would
    accidentally join Petals' swarm. We always pass use_ipfs=False
    to stay on our own private public swarm.
"""

import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Bootstrap peers
# Infrastructure addresses are deployment configuration, not source defaults.
# An empty value makes a missing environment configuration explicit instead of
# silently dialing an obsolete developer-machine peer.
# ---------------------------------------------------------------------------
DEFAULT_DISTRIBLLM_INITIAL_PEERS: list[str] = []


def get_initial_peers() -> list[str]:
    """Return comma- or newline-separated bootstrap peers from the environment."""
    raw_peers = os.environ.get("DISTRIBLLM_INITIAL_PEERS", "")
    configured_peers = [
        peer.strip()
        for peer in raw_peers.replace("\n", ",").split(",")
        if peer.strip()
    ]
    return configured_peers or list(DEFAULT_DISTRIBLLM_INITIAL_PEERS)


DISTRIBLLM_INITIAL_PEERS: list[str] = get_initial_peers()


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be true/false, yes/no, on/off, or 1/0; got {raw_value!r}"
    )


def _get_multiaddrs_env(name: str) -> tuple[str, ...]:
    raw_value = os.environ.get(name, "")
    values = tuple(
        value.strip()
        for value in raw_value.replace("\n", ",").split(",")
        if value.strip()
    )
    invalid = [value for value in values if not value.startswith("/") or any(char.isspace() for char in value)]
    if invalid:
        raise ValueError(
            f"{name} contains invalid multiaddrs: {', '.join(invalid)}"
        )
    return values


@dataclass(frozen=True)
class P2PNetworkConfig:
    """Validated process-wide Hivemind transport configuration."""

    mode: str
    port: int
    announce_maddrs: tuple[str, ...]
    trusted_relays: tuple[str, ...]
    auto_nat: bool
    nat_port_map: bool
    use_auto_relay: bool
    relay_wait_timeout: float

    @property
    def host_maddrs(self) -> list[str]:
        return [f"/ip4/0.0.0.0/tcp/{self.port}"]


def get_p2p_network_config() -> P2PNetworkConfig:
    """
    Load direct/relay settings from the environment.

    mode=auto probes direct reachability and falls back to a relay.
    mode=direct skips relay selection for intentional LAN/public operation.
    mode=relay forces outbound relay reservation for NAT-separated workers.
    """

    mode = os.environ.get("DISTRIBLLM_NETWORK_MODE", "auto").strip().lower()
    if mode not in {"auto", "direct", "relay"}:
        raise ValueError(
            "DISTRIBLLM_NETWORK_MODE must be auto, direct, or relay; "
            f"got {mode!r}"
        )

    raw_port = os.environ.get("DISTRIBLLM_P2P_PORT", "0").strip() or "0"
    try:
        port = int(raw_port)
    except ValueError as e:
        raise ValueError(
            f"DISTRIBLLM_P2P_PORT must be an integer; got {raw_port!r}"
        ) from e
    if not 0 <= port <= 65535:
        raise ValueError(
            f"DISTRIBLLM_P2P_PORT must be between 0 and 65535; got {port}"
        )

    raw_timeout = (
        os.environ.get("DISTRIBLLM_RELAY_WAIT_TIMEOUT", "60").strip() or "60"
    )
    try:
        relay_wait_timeout = float(raw_timeout)
    except ValueError as e:
        raise ValueError(
            "DISTRIBLLM_RELAY_WAIT_TIMEOUT must be a number; "
            f"got {raw_timeout!r}"
        ) from e
    if relay_wait_timeout < 0:
        raise ValueError(
            "DISTRIBLLM_RELAY_WAIT_TIMEOUT must be zero or greater"
        )

    announce_maddrs = _get_multiaddrs_env("DISTRIBLLM_ANNOUNCE_MADDRS")
    if announce_maddrs and port == 0:
        raise ValueError(
            "DISTRIBLLM_P2P_PORT must be a fixed non-zero port when "
            "DISTRIBLLM_ANNOUNCE_MADDRS is configured"
        )
    if any("/tcp/0" in address or "/udp/0" in address for address in announce_maddrs):
        raise ValueError(
            "DISTRIBLLM_ANNOUNCE_MADDRS cannot advertise port zero"
        )

    return P2PNetworkConfig(
        mode=mode,
        port=port,
        announce_maddrs=announce_maddrs,
        trusted_relays=_get_multiaddrs_env("DISTRIBLLM_TRUSTED_RELAYS"),
        auto_nat=_get_bool_env("DISTRIBLLM_AUTO_NAT", True),
        nat_port_map=_get_bool_env("DISTRIBLLM_NAT_PORT_MAP", True),
        use_auto_relay=_get_bool_env("DISTRIBLLM_AUTO_RELAY", True),
        relay_wait_timeout=relay_wait_timeout,
    )

# ---------------------------------------------------------------------------
# Supported models
# Only these models can be selected in the UI — no free text input.
# This prevents mistyped model names from causing confusing errors.
# ---------------------------------------------------------------------------
SUPPORTED_MODELS: dict[str, dict] = {
    "facebook/opt-125m": {
        "num_layers":  12,
        "hidden_size": 768,
        "gated":       False,
        "tuning":      "base",
        "description": "125M params - open base model, transport smoke test only",
        "vram_gb":     1.0,
        "gen": {
            # Small base model — collapses into loops very easily.
            # High rep penalty and tight top_k are essential.
            "temperature":        0.7,
            "top_p":              0.95,
            "top_k":              50,
            "repetition_penalty": 1.3,
            "max_new_tokens":     200,
        },
    },
    "facebook/opt-1.3b": {
        "num_layers":  24,
        "hidden_size": 2048,
        "gated":       False,
        "tuning":      "base",
        "description": "1.3B params - open base model, parity and route testing",
        "vram_gb":     3.0,
        "gen": {
            # More stable than 125m but still a base model.
            "temperature":        0.8,
            "top_p":              0.92,
            "top_k":              50,
            "repetition_penalty": 1.15,
            "max_new_tokens":     512,
        },
    },
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0": {
        "num_layers":  22,
        "hidden_size": 2048,
        "gated":       False,
        "tuning":      "chat",
        "description": "1.1B params - open chat-tuned model for local smoke tests",
        "vram_gb":     2.5,
        "gen": {
            "temperature":        0.7,
            "top_p":              0.9,
            "top_k":              40,
            "repetition_penalty": 1.1,
            "max_new_tokens":     512,
        },
    },
    "meta-llama/Llama-2-7b-hf": {
        "num_layers":  32,
        "hidden_size": 4096,
        "gated":       True,
        "tuning":      "base",
        "description": "7B params - gated Llama 2 base model for stronger route validation",
        "vram_gb":     14.0,
        "gen": {
            "temperature":        0.8,
            "top_p":              0.9,
            "top_k":              50,
            "repetition_penalty": 1.1,
            "max_new_tokens":     768,
        },
    },
    "meta-llama/Llama-2-7b-chat-hf": {
        "num_layers":  32,
        "hidden_size": 4096,
        "gated":       True,
        "tuning":      "chat",
        "description": "7B params - gated Llama 2 chat model; accepted account can download",
        "vram_gb":     14.0,
        "gen": {
            "temperature":        0.7,
            "top_p":              0.9,
            "top_k":              40,
            "repetition_penalty": 1.08,
            "max_new_tokens":     768,
        },
    },
    "meta-llama/Llama-2-13b-chat-hf": {
        "num_layers":  40,
        "hidden_size": 5120,
        "gated":       True,
        "tuning":      "chat",
        "description": "13B params - gated Llama 2 chat model for higher-quality validation",
        "vram_gb":     26.0,
        "gen": {
            "temperature":        0.7,
            "top_p":              0.9,
            "top_k":              40,
            "repetition_penalty": 1.08,
            "max_new_tokens":     1024,
        },
    },
    "meta-llama/Llama-3.2-1B": {
        "num_layers":  16,
        "hidden_size": 2048,
        "gated":       True,
        "tuning":      "instruct",
        "description": "1B params - gated Llama 3.2 instruct model; requires approval",
        "vram_gb":     2.5,
        "gen": {
            # Llama 3.2 is instruction-aware even at 1B — less prone to loops.
            "temperature":        0.8,
            "top_p":              0.9,
            "top_k":              40,
            "repetition_penalty": 1.1,
            "max_new_tokens":     512,
        },
    },
    "meta-llama/Llama-3.2-3B": {
        "num_layers":  28,
        "hidden_size": 3072,
        "gated":       True,
        "tuning":      "instruct",
        "description": "3B params - gated Llama 3.2 instruct model; requires approval",
        "vram_gb":     6.0,
        "gen": {
            "temperature":        0.8,
            "top_p":              0.9,
            "top_k":              40,
            "repetition_penalty": 1.08,
            "max_new_tokens":     768,
        },
    },
    "mistralai/Mistral-7B-v0.1": {
        "num_layers":  32,
        "hidden_size": 4096,
        "gated":       True,
        "tuning":      "base",
        "description": "7B params - gated base model; route validation, not chat-tuned",
        "vram_gb":     14.0,
        "gen": {
            # Mistral is a strong base model — stable with mild settings.
            "temperature":        0.85,
            "top_p":              0.9,
            "top_k":              50,
            "repetition_penalty": 1.1,
            "max_new_tokens":     1024,
        },
    },
}

# Fallback used when a model isn't in SUPPORTED_MODELS
DEFAULT_GEN_CONFIG: dict = {
    "temperature":        0.8,
    "top_p":              0.92,
    "top_k":              50,
    "repetition_penalty": 1.1,
    "max_new_tokens":     512,
}

DHT_PREFIX        = "distribllm"
DHT_EXPIRY_TIME   = 60
ANNOUNCE_INTERVAL = 30
