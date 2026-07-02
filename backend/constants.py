"""
constants.py
Single source of truth for DistribLLM network configuration.

Bootstrap peers:
    These are the well-known stable entry points for the swarm.
    Run bootstrap.py ONCE with --identity_path bootstrap.id to generate
    a stable peer ID, then hardcode the printed address here.

    Until you have a VPS, run bootstrap.py locally and use the
    /ip4/127.0.0.1/... address for single-machine testing.

use_ipfs=False:
    Hivemind's P2P layer has a use_ipfs flag. When True, it connects
    to the public IPFS/Petals network — meaning your nodes would
    accidentally join Petals' swarm. We always pass use_ipfs=False
    to stay on our own private public swarm.
"""

# ---------------------------------------------------------------------------
# Bootstrap peers
# Replace this with your VPS address once you have one.
# Run: python3 bootstrap.py --port 7001 --identity_path bootstrap.id
# Then copy the printed /ip4/<YOUR_IP>/tcp/7001/p2p/<PEER_ID> here.
# ---------------------------------------------------------------------------
DISTRIBLLM_INITIAL_PEERS: list[str] = [
    "/ip4/127.0.0.1/tcp/7001/p2p/QmY54qgx7Si9KCWXFVdrn4J7kGPfdJUoNHNeTy1JqnDnX7",
    "/ip4/172.27.32.227/tcp/7001/p2p/QmY54qgx7Si9KCWXFVdrn4J7kGPfdJUoNHNeTy1JqnDnX7"
]

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
        "description": "125M params — open model, no token needed, good for testing",
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
        "description": "1.3B params — open model, decent quality",
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
    "meta-llama/Llama-3.2-1B": {
        "num_layers":  16,
        "hidden_size": 2048,
        "gated":       True,
        "description": "1B params — requires HuggingFace token and license approval",
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
        "description": "3B params — requires HuggingFace token and license approval",
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
        "description": "7B params — requires HuggingFace token",
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
