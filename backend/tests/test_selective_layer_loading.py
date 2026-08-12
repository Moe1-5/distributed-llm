from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import weakref
import gc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
import torch.nn as nn
from safetensors.torch import save_file
from transformers import LlamaConfig, MistralConfig, OPTConfig
from transformers.models.llama.modeling_llama import LlamaDecoderLayer
from transformers.models.llama.modeling_llama import LlamaRotaryEmbedding
from transformers.models.mistral.modeling_mistral import MistralDecoderLayer
from transformers.models.opt.modeling_opt import OPTDecoderLayer

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from node import block_loader
from node.handler import InferenceHandler


def _write_sharded_layers(
    root: Path,
    config: OPTConfig | LlamaConfig | MistralConfig,
    layer_class: type[nn.Module],
    prefix: str,
) -> list[dict[str, torch.Tensor]]:
    config.save_pretrained(root)
    weight_map: dict[str, str] = {}
    states: list[dict[str, torch.Tensor]] = []
    total_size = 0
    for index in range(config.num_hidden_layers):
        torch.manual_seed(100 + index)
        layer = layer_class(config, index).eval()
        state = {
            name: tensor.detach().clone().contiguous()
            for name, tensor in layer.state_dict().items()
        }
        states.append(state)
        shard_name = f"model-{index + 1:05d}-of-{config.num_hidden_layers:05d}.safetensors"
        shard = {f"{prefix}{index}.{name}": tensor for name, tensor in state.items()}
        save_file(shard, root / shard_name)
        for name, tensor in shard.items():
            weight_map[name] = shard_name
            total_size += tensor.numel() * tensor.element_size()
    (root / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": total_size}, "weight_map": weight_map}),
        encoding="utf-8",
    )
    return states


class SelectiveLayerLoadingTests(unittest.TestCase):
    def test_indexed_opt_load_opens_only_selected_shard_and_matches_output(self) -> None:
        config = OPTConfig(
            hidden_size=16,
            ffn_dim=32,
            num_attention_heads=4,
            num_hidden_layers=3,
            dropout=0.0,
            attention_dropout=0.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            states = _write_sharded_layers(
                root, config, OPTDecoderLayer, "model.decoder.layers."
            )
            opened: list[str] = []
            real_safe_open = block_loader.safe_open

            def tracking_open(path, *args, **kwargs):
                opened.append(Path(path).name)
                return real_safe_open(path, *args, **kwargs)

            with (
                patch("node.block_loader.safe_open", side_effect=tracking_open),
                patch.object(
                    block_loader.AutoModelForCausalLM,
                    "from_pretrained",
                    side_effect=AssertionError("full model must not be instantiated"),
                ) as full_loader,
            ):
                loaded = block_loader.load_layers(
                    "synthetic/opt",
                    1,
                    2,
                    device="cpu",
                    dtype=torch.float32,
                    local_model_path=str(root),
                )

            full_loader.assert_not_called()
            self.assertEqual(opened, ["model-00002-of-00003.safetensors"])
            self.assertEqual(len(loaded), 1)
            for name, expected in states[1].items():
                torch.testing.assert_close(loaded[0].state_dict()[name], expected)

            direct = OPTDecoderLayer(config, 1).eval()
            direct.load_state_dict(states[1])
            hidden = torch.randn(1, 4, config.hidden_size)
            torch.testing.assert_close(loaded[0](hidden)[0], direct(hidden)[0])

            diagnostics = loaded.load_diagnostics
            self.assertEqual(diagnostics["strategy"], "selective_safetensors")
            self.assertEqual(diagnostics["source_shards"], ("model-00002-of-00003.safetensors",))
            self.assertLess(
                diagnostics["loaded_parameter_bytes"], diagnostics["checkpoint_total_bytes"]
            )

    def test_llama_boundaries_load_exact_requested_blocks(self) -> None:
        config = LlamaConfig(
            hidden_size=16,
            intermediate_size=32,
            num_attention_heads=4,
            num_key_value_heads=4,
            num_hidden_layers=3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            states = _write_sharded_layers(root, config, LlamaDecoderLayer, "model.layers.")
            for start, end in ((0, 1), (1, 2), (2, 3), (0, 3)):
                with self.subTest(start=start, end=end):
                    loaded = block_loader.load_layers(
                        "synthetic/llama",
                        start,
                        end,
                        device="cpu",
                        dtype=torch.float32,
                        local_model_path=str(root),
                    )
                    self.assertEqual(len(loaded), end - start)
                    for relative, global_index in enumerate(range(start, end)):
                        for name, expected in states[global_index].items():
                            torch.testing.assert_close(
                                loaded[relative].state_dict()[name], expected
                            )
                    if start == 1 and end == 2:
                        direct = LlamaDecoderLayer(config, 1).eval()
                        direct.load_state_dict(states[1])
                        hidden = torch.randn(1, 4, config.hidden_size)
                        position_ids = torch.arange(4).unsqueeze(0)
                        rotary = LlamaRotaryEmbedding(config=config)
                        position_embeddings = rotary(hidden, position_ids)
                        torch.testing.assert_close(
                            loaded[0](
                                hidden,
                                position_ids=position_ids,
                                position_embeddings=position_embeddings,
                            ),
                            direct(
                                hidden,
                                position_ids=position_ids,
                                position_embeddings=position_embeddings,
                            ),
                        )

    def test_mistral_layer_contract_loads_exact_state(self) -> None:
        config = MistralConfig(
            hidden_size=16,
            intermediate_size=32,
            num_attention_heads=4,
            num_key_value_heads=4,
            num_hidden_layers=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            states = _write_sharded_layers(root, config, MistralDecoderLayer, "model.layers.")
            loaded = block_loader.load_layers(
                "synthetic/mistral",
                1,
                2,
                device="cpu",
                dtype=torch.float32,
                local_model_path=str(root),
            )
            for name, expected in states[1].items():
                torch.testing.assert_close(loaded[0].state_dict()[name], expected)

    def test_malformed_selected_tensor_fails_before_layers_are_returned(self) -> None:
        config = OPTConfig(
            hidden_size=16,
            ffn_dim=32,
            num_attention_heads=4,
            num_hidden_layers=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            states = _write_sharded_layers(
                root, config, OPTDecoderLayer, "model.decoder.layers."
            )
            bad_state = states[0].copy()
            first_name = next(iter(bad_state))
            bad_state[first_name] = bad_state[first_name][:-1].contiguous()
            save_file(
                {f"model.decoder.layers.0.{name}": value for name, value in bad_state.items()},
                root / "model-00001-of-00001.safetensors",
            )

            with self.assertRaisesRegex(block_loader.SelectiveLoadError, "has shape"):
                block_loader.load_layers(
                    "synthetic/opt",
                    0,
                    1,
                    device="cpu",
                    dtype=torch.float32,
                    local_model_path=str(root),
                )

    def test_binary_checkpoint_uses_reported_compatibility_fallback(self) -> None:
        config = OPTConfig(
            hidden_size=16,
            ffn_dim=32,
            num_attention_heads=4,
            num_hidden_layers=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config.save_pretrained(root)
            (root / "pytorch_model.bin").write_bytes(b"compatibility fixture")
            fake_model = nn.Module()
            fake_model.model = nn.Module()
            fake_model.model.decoder = nn.Module()
            fake_model.model.decoder.layers = nn.ModuleList(
                [OPTDecoderLayer(config, index) for index in range(2)]
            )

            with patch.object(
                block_loader.AutoModelForCausalLM,
                "from_pretrained",
                return_value=fake_model,
            ) as full_loader:
                loaded = block_loader.load_layers(
                    "synthetic/opt-bin",
                    1,
                    2,
                    device="cpu",
                    dtype=torch.float32,
                    local_model_path=str(root),
                )

            full_loader.assert_called_once()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded.load_diagnostics["strategy"], "full_model_fallback")
            self.assertIn("PyTorch binary", loaded.load_diagnostics["fallback_reason"])

    def test_binary_checkpoint_can_be_rejected_by_policy(self) -> None:
        config = OPTConfig(
            hidden_size=16,
            ffn_dim=32,
            num_attention_heads=4,
            num_hidden_layers=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config.save_pretrained(root)
            (root / "pytorch_model.bin").write_bytes(b"compatibility fixture")
            with patch.dict(os.environ, {"DISTRIBLLM_ALLOW_FULL_MODEL_FALLBACK": "false"}):
                with self.assertRaisesRegex(
                    block_loader.SelectiveLoadError, "fallback is disabled"
                ):
                    block_loader.load_layers(
                        "synthetic/opt-bin",
                        0,
                        1,
                        device="cpu",
                        dtype=torch.float32,
                        local_model_path=str(root),
                    )

    def test_duplicate_index_keys_are_rejected(self) -> None:
        config = OPTConfig(
            hidden_size=16,
            ffn_dim=32,
            num_attention_heads=4,
            num_hidden_layers=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config.save_pretrained(root)
            (root / "model.safetensors.index.json").write_text(
                '{"weight_map": {}, "weight_map": {}}', encoding="utf-8"
            )
            with self.assertRaisesRegex(block_loader.SelectiveLoadError, "Duplicate JSON key"):
                block_loader.load_layers(
                    "synthetic/opt",
                    0,
                    1,
                    device="cpu",
                    dtype=torch.float32,
                    local_model_path=str(root),
                )

    def test_handler_unload_releases_layers_and_diagnostics(self) -> None:
        config = OPTConfig(
            hidden_size=16,
            ffn_dim=32,
            num_attention_heads=4,
            num_hidden_layers=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sharded_layers(root, config, OPTDecoderLayer, "model.decoder.layers.")
            handler = InferenceHandler(
                "synthetic/opt",
                0,
                1,
                device="cpu",
                dtype=torch.float32,
                local_model_path=str(root),
            )
            handler.load()
            layer_reference = weakref.ref(handler.layers[0])
            self.assertEqual(handler.get_info()["loading"]["strategy"], "selective_safetensors")
            handler.unload()
            gc.collect()
            self.assertIsNone(layer_reference())
            self.assertIsNone(handler.get_info()["loading"])

    def test_local_index_cannot_escape_model_directory(self) -> None:
        config = OPTConfig(
            hidden_size=16,
            ffn_dim=32,
            num_attention_heads=4,
            num_hidden_layers=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "model"
            _write_sharded_layers(root, config, OPTDecoderLayer, "model.decoder.layers.")
            index_path = root / "model.safetensors.index.json"
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            payload["weight_map"] = {
                name: "../outside.safetensors" for name in payload["weight_map"]
            }
            index_path.write_text(json.dumps(payload), encoding="utf-8")
            (root.parent / "outside.safetensors").write_bytes(b"outside")

            with self.assertRaisesRegex(block_loader.SelectiveLoadError, "outside"):
                block_loader.load_layers(
                    "synthetic/opt",
                    0,
                    1,
                    device="cpu",
                    dtype=torch.float32,
                    local_model_path=str(root),
                )


if __name__ == "__main__":
    unittest.main()
