import asyncio
import sys
from pathlib import Path

import torch
import torch.nn as nn

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from api import server as api_server
from client.generation import DistributedGenerator, _ContextAwareTextDecoder


class ContextTokenizer:
    eos_token_id = 0

    def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
        return torch.tensor([[1, 2]])

    def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
        ids = [int(token_id) for token_id in token_ids]
        visible = [token_id for token_id in ids if token_id != self.eos_token_id]
        return {
            (): "",
            (3,): "Hello",
            (3, 4): "Hello world",
        }.get(tuple(visible), "Hello world")


def test_chat_model_applies_publisher_template_exactly_once():
    class ChatTokenizer:
        def __init__(self) -> None:
            self.calls = []

        def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
            raise AssertionError("chat prompts must not use raw encode")

        def apply_chat_template(self, messages, **kwargs) -> torch.Tensor:
            self.calls.append((messages, kwargs))
            return torch.tensor([[10, 11, 12]])

    tokenizer = ChatTokenizer()
    generator = DistributedGenerator(
        "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        sequential=object(),
    )
    generator.tokenizer = tokenizer

    encoded = generator._encode_prompt("Explain relay mode")

    assert encoded.tolist() == [[10, 11, 12]]
    assert len(tokenizer.calls) == 1
    messages, kwargs = tokenizer.calls[0]
    assert messages == [{"role": "user", "content": "Explain relay mode"}]
    assert kwargs == {
        "tokenize": True,
        "add_generation_prompt": True,
        "return_tensors": "pt",
    }


def test_base_model_keeps_raw_completion_prompt():
    class BaseTokenizer:
        def __init__(self) -> None:
            self.encoded_prompts = []

        def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
            self.encoded_prompts.append(prompt)
            return torch.tensor([[7, 8]])

        def apply_chat_template(self, messages, **kwargs) -> torch.Tensor:
            raise AssertionError("base prompts must not use a chat template")

    tokenizer = BaseTokenizer()
    generator = DistributedGenerator("facebook/opt-125m", sequential=object())
    generator.tokenizer = tokenizer

    encoded = generator._encode_prompt("Once upon a time")

    assert encoded.tolist() == [[7, 8]]
    assert tokenizer.encoded_prompts == ["Once upon a time"]


def test_context_decoder_reconstructs_full_decode_with_spacing():
    tokenizer = ContextTokenizer()
    decoder = _ContextAwareTextDecoder(tokenizer)

    chunks = [decoder.push(3), decoder.push(4), decoder.finish()]

    assert "".join(chunks) == tokenizer.decode([3, 4], skip_special_tokens=True)
    assert "".join(chunks) == "Hello world"


def test_generate_stream_chunks_match_cumulative_tokenizer_decode():
    class IdentitySequential:
        def forward(self, hidden_states, attention_mask=None, position_ids=None):
            return hidden_states, ["peer (layers 0-1)"]

    tokenizer = ContextTokenizer()
    generator = DistributedGenerator(
        "facebook/opt-125m",
        sequential=IdentitySequential(),
        device="cpu",
    )
    generator._loaded = True
    generator.tokenizer = tokenizer
    generator.embed_tokens = nn.Embedding(16, 4)
    generator.position_embeddings = nn.Embedding(16, 4)
    generator.norm = nn.Identity()
    generator.lm_head = nn.Linear(4, 16)
    sampled_ids = iter((3, 4, 0))
    generator._sample = lambda logits, **kwargs: torch.tensor([[next(sampled_ids)]])

    async def collect() -> list[dict]:
        return [
            chunk
            async for chunk in generator.generate_stream(
                "hello",
                max_new_tokens=3,
                do_sample=False,
            )
        ]

    chunks = asyncio.run(collect())
    streamed_text = "".join(chunk["token"] for chunk in chunks if "token" in chunk)

    assert streamed_text == tokenizer.decode([3, 4, 0], skip_special_tokens=True)
    assert chunks[-1]["done"] is True


def test_local_nodes_endpoint_returns_only_process_owned_nodes():
    class LocalNode:
        node_id = "local-node"

        def get_info(self) -> dict:
            return {
                "node_id": self.node_id,
                "peer_id": "local-peer",
                "model_name": "facebook/opt-125m",
                "layer_start": 0,
                "layer_end": 6,
                "device": "cpu",
                "running": True,
                "maddrs": [],
                "layers_loaded": True,
                "rpc_running": True,
            }

    original_node = api_server.node
    original_nodes = dict(api_server.local_nodes)
    try:
        api_server.local_nodes.clear()
        api_server.local_nodes["local-node"] = LocalNode()
        api_server.node = api_server.local_nodes["local-node"]

        result = asyncio.run(api_server.get_local_nodes())
    finally:
        api_server.local_nodes.clear()
        api_server.local_nodes.update(original_nodes)
        api_server.node = original_node

    assert [node["peer_id"] for node in result["nodes"]] == ["local-peer"]
