import asyncio
import json
import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
import torch.nn as nn

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.generation import DistributedGenerator
from client.sequential import RemoteSequential
from models.architecture_adapter import get_architecture_adapter
from node.handler import InferenceHandler
from node.node import Node
from node.rpc_server import RPCServer
from api.local_models import (
    LocalModelValidationError,
    import_local_model,
    inspect_local_model,
    get_local_model_path,
    list_local_models,
    remove_local_model,
    validate_registered_local_model,
    validate_local_model_directory,
)
from api import hf_oauth
from api import settings as hf_settings
from constants import DEFAULT_DISTRIBLLM_INITIAL_PEERS, SUPPORTED_MODELS, get_initial_peers


class InitialPeersTests(unittest.TestCase):
    def test_reads_comma_and_newline_separated_peers_from_environment(self):
        with patch.dict(
            os.environ,
            {"DISTRIBLLM_INITIAL_PEERS": "peer-a, peer-b\npeer-c"},
        ):
            self.assertEqual(get_initial_peers(), ["peer-a", "peer-b", "peer-c"])

    def test_empty_environment_value_uses_default_peers(self):
        with patch.dict(os.environ, {"DISTRIBLLM_INITIAL_PEERS": ""}):
            self.assertEqual(get_initial_peers(), DEFAULT_DISTRIBLLM_INITIAL_PEERS)


class DummyDHT:
    pass


class DummyDHTResult:
    def __init__(self, value):
        self.value = value


class MappingDHT:
    def __init__(self, values: dict[str, object]):
        self.values = values

    def get(self, key: str, latest: bool = True) -> DummyDHTResult | None:
        value = self.values.get(key)
        return DummyDHTResult(value) if value is not None else None


def write_local_model_fixture(
    directory: Path,
    *,
    model_type: str = "llama",
    num_layers: int = 16,
    hidden_size: int = 2048,
    tokenizer: bool = True,
    weights: bool = True,
    sharded: bool = False,
    missing_shard: bool = False,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(
        json.dumps(
            {
                "model_type": model_type,
                "architectures": ["LlamaForCausalLM"],
                "num_hidden_layers": num_layers,
                "hidden_size": hidden_size,
            }
        ),
        encoding="utf-8",
    )
    if tokenizer:
        (directory / "tokenizer.json").write_text("{}", encoding="utf-8")
    if not weights:
        return
    if sharded:
        (directory / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"model.layers.0.weight": "model-00001.safetensors"}}),
            encoding="utf-8",
        )
        if not missing_shard:
            (directory / "model-00001.safetensors").write_bytes(b"stub")
    else:
        (directory / "model.safetensors").write_bytes(b"stub")


class LocalModelImportTests(unittest.TestCase):
    def test_valid_local_model_directory_is_accepted_without_loading_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "llama"
            write_local_model_fixture(model_dir)

            result = validate_local_model_directory("meta-llama/Llama-3.2-1B", str(model_dir))

            self.assertTrue(result["valid"])
            self.assertEqual(result["model_name"], "meta-llama/Llama-3.2-1B")
            self.assertEqual(result["config"]["num_layers"], 16)
            self.assertEqual(result["config"]["hidden_size"], 2048)
            self.assertEqual(result["weight_file_count"], 1)

    def test_llama_style_sprint_11_models_accept_matching_local_snapshots(self) -> None:
        cases = [
            ("TinyLlama/TinyLlama-1.1B-Chat-v1.0", 22, 2048),
            ("meta-llama/Llama-2-7b-chat-hf", 32, 4096),
            ("meta-llama/Llama-2-13b-chat-hf", 40, 5120),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for model_name, num_layers, hidden_size in cases:
                with self.subTest(model_name=model_name):
                    model_dir = Path(tmp) / model_name.replace("/", "__")
                    write_local_model_fixture(
                        model_dir,
                        num_layers=num_layers,
                        hidden_size=hidden_size,
                    )

                    result = validate_local_model_directory(model_name, str(model_dir))

                    self.assertTrue(result["valid"])
                    self.assertEqual(result["config"]["model_type"], "llama")
                    self.assertEqual(result["config"]["num_layers"], num_layers)
                    self.assertEqual(result["config"]["hidden_size"], hidden_size)

    def test_local_model_validation_rejects_wrong_model_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "wrong"
            write_local_model_fixture(model_dir, num_layers=28)

            with self.assertRaisesRegex(LocalModelValidationError, "16 layers"):
                validate_local_model_directory("meta-llama/Llama-3.2-1B", str(model_dir))

    def test_local_model_validation_rejects_missing_tokenizer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "no-tokenizer"
            write_local_model_fixture(model_dir, tokenizer=False)

            with self.assertRaisesRegex(LocalModelValidationError, "tokenizer"):
                validate_local_model_directory("meta-llama/Llama-3.2-1B", str(model_dir))

    def test_local_model_validation_rejects_incomplete_shards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "incomplete"
            write_local_model_fixture(model_dir, sharded=True, missing_shard=True)

            with self.assertRaisesRegex(LocalModelValidationError, "missing shard"):
                validate_local_model_directory("meta-llama/Llama-3.2-1B", str(model_dir))

    def test_local_model_validation_accepts_complete_safetensors_with_stale_bin_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "mixed-formats"
            write_local_model_fixture(model_dir, sharded=True)
            (model_dir / "pytorch_model.bin.index.json").write_text(
                json.dumps(
                    {
                        "weight_map": {
                            "model.layers.0.self_attn.q_proj.weight": "pytorch_model-00001-of-00002.bin",
                            "model.layers.31.mlp.down_proj.weight": "pytorch_model-00002-of-00002.bin",
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = validate_local_model_directory("meta-llama/Llama-3.2-1B", str(model_dir))

            self.assertTrue(result["valid"])
            self.assertEqual(result["weight_format"], "safetensors")
            self.assertEqual(result["weight_files"], ["model-00001.safetensors"])

    def test_local_model_validation_rejects_huggingface_url_as_path(self) -> None:
        for path in (
            "https://huggingface.co/meta-llama/Llama-3.2-1B",
            "http://huggingface.co/meta-llama/Llama-3.2-1B",
            "huggingface.co/meta-llama/Llama-3.2-1B",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(LocalModelValidationError, "not a Hugging Face URL"):
                    validate_local_model_directory("meta-llama/Llama-3.2-1B", path)

    def test_local_model_import_and_list_omit_raw_path(self) -> None:
        from api import local_models

        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            model_dir = Path(tmp) / "llama"
            write_local_model_fixture(model_dir)
            original_registry = local_models._REGISTRY_FILE
            local_models._REGISTRY_FILE = registry_path
            try:
                imported = import_local_model("meta-llama/Llama-3.2-1B", str(model_dir))
                listed = list_local_models()
            finally:
                local_models._REGISTRY_FILE = original_registry

        self.assertNotIn("path", imported)
        self.assertNotIn("path", listed[0])

    def test_remove_local_model_can_delete_managed_huggingface_snapshot(self) -> None:
        from api import local_models

        class FakeRevision:
            commit_hash = "abc123"
            snapshot_path = None

        class FakeRepo:
            repo_type = "model"
            repo_id = "meta-llama/Llama-3.2-1B"

            def __init__(self, snapshot_path: Path) -> None:
                revision = FakeRevision()
                revision.snapshot_path = snapshot_path
                self.revisions = {revision}

        class FakeStrategy:
            expected_freed_size = 1234
            expected_freed_size_str = "1.2 KB"

            def execute(self) -> None:
                deleted.append("executed")

        class FakeCacheInfo:
            def __init__(self, snapshot_path: Path) -> None:
                self.repos = {FakeRepo(snapshot_path)}

            def delete_revisions(self, *commit_hashes: str) -> FakeStrategy:
                deleted.extend(commit_hashes)
                return FakeStrategy()

        deleted: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            model_dir = Path(tmp) / "llama"
            write_local_model_fixture(model_dir)
            original_registry = local_models._REGISTRY_FILE
            original_scan_cache_dir = local_models.scan_cache_dir
            local_models._REGISTRY_FILE = registry_path
            local_models.scan_cache_dir = lambda: FakeCacheInfo(model_dir.resolve())
            try:
                import_local_model("meta-llama/Llama-3.2-1B", str(model_dir))
                result = remove_local_model("meta-llama/Llama-3.2-1B", delete_files=True)
                listed = list_local_models()
            finally:
                local_models._REGISTRY_FILE = original_registry
                local_models.scan_cache_dir = original_scan_cache_dir

        self.assertTrue(result["removed"])
        self.assertTrue(result["files_deleted"])
        self.assertEqual(result["deleted_bytes"], 1234)
        self.assertEqual(deleted, ["abc123", "executed"])
        self.assertEqual(listed, [])

    def test_remove_local_model_deletes_cache_even_without_registry_entry(self) -> None:
        from api import local_models

        class FakeRevision:
            def __init__(self, commit_hash: str, snapshot_path: Path) -> None:
                self.commit_hash = commit_hash
                self.snapshot_path = snapshot_path

        class FakeRepo:
            repo_type = "model"
            repo_id = "meta-llama/Llama-3.2-1B"

            def __init__(self, root: Path) -> None:
                self.revisions = {
                    FakeRevision("rev-a", root / "snapshots" / "rev-a"),
                    FakeRevision("rev-b", root / "snapshots" / "rev-b"),
                }

        class FakeStrategy:
            expected_freed_size = 4096
            expected_freed_size_str = "4.0 KB"

            def execute(self) -> None:
                deleted.append("executed")

        class FakeCacheInfo:
            def __init__(self, root: Path) -> None:
                self.repos = {FakeRepo(root)}

            def delete_revisions(self, *commit_hashes: str) -> FakeStrategy:
                deleted.extend(commit_hashes)
                return FakeStrategy()

        deleted: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            original_registry = local_models._REGISTRY_FILE
            original_scan_cache_dir = local_models.scan_cache_dir
            local_models._REGISTRY_FILE = registry_path
            local_models.scan_cache_dir = lambda: FakeCacheInfo(Path(tmp) / "cache")
            try:
                result = remove_local_model("meta-llama/Llama-3.2-1B", delete_files=True)
            finally:
                local_models._REGISTRY_FILE = original_registry
                local_models.scan_cache_dir = original_scan_cache_dir

        self.assertTrue(result["removed"])
        self.assertFalse(result["registry_removed"])
        self.assertTrue(result["files_deleted"])
        self.assertEqual(set(deleted[:-1]), {"rev-a", "rev-b"})
        self.assertEqual(deleted[-1], "executed")

    def test_remove_local_model_does_not_delete_arbitrary_manual_folder(self) -> None:
        from api import local_models

        class FakeCacheInfo:
            repos = set()

        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            model_dir = Path(tmp) / "llama"
            write_local_model_fixture(model_dir)
            original_registry = local_models._REGISTRY_FILE
            original_scan_cache_dir = local_models.scan_cache_dir
            local_models._REGISTRY_FILE = registry_path
            local_models.scan_cache_dir = lambda: FakeCacheInfo()
            try:
                import_local_model("meta-llama/Llama-3.2-1B", str(model_dir))
                result = remove_local_model("meta-llama/Llama-3.2-1B", delete_files=True)
            finally:
                local_models._REGISTRY_FILE = original_registry
                local_models.scan_cache_dir = original_scan_cache_dir

            self.assertTrue((model_dir / "config.json").exists())

        self.assertTrue(result["removed"])
        self.assertFalse(result["files_deleted"])
        self.assertIn("not a managed Hugging Face cache snapshot", result["message"])

    def test_local_model_inspect_omits_raw_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "llama"
            write_local_model_fixture(model_dir)

            inspected = inspect_local_model("meta-llama/Llama-3.2-1B", str(model_dir))

        self.assertTrue(inspected["valid"])
        self.assertNotIn("path", inspected)
        self.assertEqual(inspected["message"], "Local model directory is valid for offline loading.")

    def test_local_model_settings_endpoints_do_not_leak_path_by_default(self) -> None:
        from api import local_models
        from api import server as api_server

        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            model_dir = Path(tmp) / "llama"
            write_local_model_fixture(model_dir)
            original_registry = local_models._REGISTRY_FILE
            local_models._REGISTRY_FILE = registry_path
            try:
                inspected = asyncio.run(
                    api_server.inspect_local_model_directory(
                        api_server.LocalModelRequest(
                            model_name="meta-llama/Llama-3.2-1B",
                            path=str(model_dir),
                        )
                    )
                )
                imported = asyncio.run(
                    api_server.add_local_model(
                        api_server.LocalModelRequest(
                            model_name="meta-llama/Llama-3.2-1B",
                            path=str(model_dir),
                        )
                    )
                )
                listed = asyncio.run(api_server.get_local_models())
                specific = asyncio.run(api_server.get_local_model("meta-llama/Llama-3.2-1B"))
            finally:
                local_models._REGISTRY_FILE = original_registry

        self.assertNotIn("path", inspected)
        self.assertNotIn("path", imported["model"])
        self.assertNotIn("path", listed["models"][0])
        self.assertEqual(specific["model"]["path"], str(model_dir.resolve()))

    def test_registered_local_model_path_is_revalidated_before_use(self) -> None:
        from api import local_models

        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            model_dir = Path(tmp) / "llama"
            write_local_model_fixture(model_dir)
            original_registry = local_models._REGISTRY_FILE
            local_models._REGISTRY_FILE = registry_path
            try:
                import_local_model("meta-llama/Llama-3.2-1B", str(model_dir))
                self.assertEqual(
                    get_local_model_path("meta-llama/Llama-3.2-1B"),
                    str(model_dir.resolve()),
                )
                (model_dir / "tokenizer.json").unlink()
                with self.assertRaisesRegex(LocalModelValidationError, "tokenizer"):
                    validate_registered_local_model("meta-llama/Llama-3.2-1B")
                with self.assertRaisesRegex(LocalModelValidationError, "tokenizer"):
                    get_local_model_path("meta-llama/Llama-3.2-1B")
            finally:
                local_models._REGISTRY_FILE = original_registry

    def test_gated_node_start_uses_imported_local_path_without_token(self) -> None:
        from api import server as api_server

        captured: dict[str, object] = {}

        class FakeNode:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)
                self.node_id = "fake-node"
                self.dht_prefix = kwargs["dht_prefix"]
                self.model_name = kwargs["model_name"]
                self.layer_start = kwargs["layer_start"]
                self.layer_end = kwargs["layer_end"]

            def start(self) -> None:
                captured["started"] = True

            def is_running(self) -> bool:
                return True

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                    "device": "cpu",
                    "running": True,
                    "maddrs": [],
                    "layers_loaded": True,
                    "rpc_running": True,
                }

        original_node_cls = api_server.Node
        original_get_path = api_server.get_local_model_path
        original_get_token = api_server.get_hf_token
        original_local_nodes = dict(api_server.local_nodes)
        original_node = api_server.node
        api_server.Node = FakeNode
        api_server.get_local_model_path = lambda model_name: "/local/llama"
        api_server.get_hf_token = lambda: "hf_should_not_be_used"
        api_server.local_nodes.clear()
        api_server.node = None
        try:
            result = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="meta-llama/Llama-3.2-1B",
                        layer_start=0,
                        layer_end=1,
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.Node = original_node_cls
            api_server.get_local_model_path = original_get_path
            api_server.get_hf_token = original_get_token
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(result["status"], "started")
        self.assertEqual(captured["local_model_path"], "/local/llama")
        self.assertIsNone(captured["hf_token"])

    def test_gated_node_start_reports_invalid_registered_local_import(self) -> None:
        from api import server as api_server

        original_get_path = api_server.get_local_model_path
        api_server.get_local_model_path = lambda model_name: (_ for _ in ()).throw(
            LocalModelValidationError(
                "missing_tokenizer_files",
                "Local model directory must include tokenizer.json.",
            )
        )
        try:
            result = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="meta-llama/Llama-3.2-1B",
                        layer_start=0,
                        layer_end=1,
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.get_local_model_path = original_get_path

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "local_model_import_invalid")
        self.assertEqual(result["validation"]["error"], "missing_tokenizer_files")

    def test_gated_generator_start_uses_imported_local_path_without_token(self) -> None:
        from api import server as api_server

        captured: dict[str, object] = {}

        class FakeDHT:
            peer_id = "fake-client-peer"

            def __init__(self, *args, **kwargs) -> None:
                captured["dht_kwargs"] = kwargs

            def shutdown(self) -> None:
                captured["dht_shutdown"] = True

        class FakeSequential:
            def __init__(self, **kwargs) -> None:
                captured["sequential_kwargs"] = kwargs

        class FakeGenerator:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)
                self.loaded = False
                self.model_name = kwargs["model_name"]
                self.sequential = kwargs["sequential"]

            def load(self) -> None:
                self.loaded = True

            def is_loaded(self) -> bool:
                return self.loaded

        original_generator_cls = api_server.DistributedGenerator
        original_dht_cls = api_server.hivemind.DHT
        original_sequential_cls = api_server.RemoteSequential
        original_get_path = api_server.get_local_model_path
        original_get_token = api_server.get_hf_token
        original_generator = api_server.generator
        original_client_dht = api_server.client_dht
        api_server.DistributedGenerator = FakeGenerator
        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.get_local_model_path = lambda model_name: "/local/llama"
        api_server.get_hf_token = lambda: "hf_should_not_be_used"
        api_server.generator = None
        api_server.client_dht = None
        try:
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(
                        model_name="meta-llama/Llama-3.2-1B",
                    )
                )
            )
        finally:
            api_server.DistributedGenerator = original_generator_cls
            api_server.hivemind.DHT = original_dht_cls
            api_server.RemoteSequential = original_sequential_cls
            api_server.get_local_model_path = original_get_path
            api_server.get_hf_token = original_get_token
            api_server.generator = original_generator
            if api_server.client_dht is not None and api_server.client_dht is not original_client_dht:
                api_server.client_dht = None
            api_server.client_dht = original_client_dht

        self.assertEqual(result["status"], "ready")
        self.assertEqual(captured["local_model_path"], "/local/llama")
        self.assertIsNone(captured["hf_token"])

    def test_gated_generator_start_reports_invalid_registered_local_import(self) -> None:
        from api import server as api_server

        original_get_path = api_server.get_local_model_path
        original_generator = api_server.generator
        original_client_dht = api_server.client_dht
        api_server.generator = None
        api_server.client_dht = None
        api_server.get_local_model_path = lambda model_name: (_ for _ in ()).throw(
            LocalModelValidationError(
                "path_not_found",
                "Local model path does not exist: /missing/model",
            )
        )
        try:
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(
                        model_name="meta-llama/Llama-3.2-1B",
                    )
                )
            )
        finally:
            api_server.get_local_model_path = original_get_path
            api_server.generator = original_generator
            api_server.client_dht = original_client_dht

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "local_model_import_invalid")
        self.assertEqual(result["validation"]["error"], "path_not_found")
        self.assertIsNone(api_server.client_dht)


class HuggingFaceOAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        hf_oauth._device_flows.clear()
        hf_oauth._download_jobs.clear()

    def tearDown(self) -> None:
        hf_oauth._device_flows.clear()
        hf_oauth._download_jobs.clear()

    def test_device_flow_start_returns_user_code_without_device_code(self) -> None:
        original_client_id = hf_oauth._oauth_client_id
        original_post_form = hf_oauth._post_form
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_post_form(url: str, data: dict[str, str]) -> dict:
            calls.append((url, data))
            return {
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://huggingface.co/oauth/device",
                "verification_uri_complete": "https://huggingface.co/oauth/device?user_code=ABCD-EFGH",
                "expires_in": 600,
                "interval": 5,
            }

        hf_oauth._oauth_client_id = lambda: "client-id"
        hf_oauth._post_form = fake_post_form
        try:
            result = hf_oauth.start_huggingface_device_flow()
        finally:
            hf_oauth._oauth_client_id = original_client_id
            hf_oauth._post_form = original_post_form

        self.assertEqual(result["user_code"], "ABCD-EFGH")
        self.assertEqual(result["verification_uri"], "https://huggingface.co/oauth/device")
        self.assertIn("flow_id", result)
        self.assertNotIn("device_code", result)
        self.assertEqual(calls[0][1]["scope"], hf_oauth.HF_OAUTH_SCOPE)

    def test_device_flow_poll_saves_token_without_returning_it(self) -> None:
        original_post_form = hf_oauth._post_form
        original_save_token = hf_oauth.save_hf_token
        original_public_connection = hf_oauth._public_connection
        saved_tokens: list[str] = []

        flow = hf_oauth.DeviceFlow(
            flow_id="flow-1",
            device_code="device-secret",
            user_code="ABCD-EFGH",
            verification_uri="https://huggingface.co/oauth/device",
            verification_uri_complete=None,
            expires_at=time.time() + 300,
            interval=5,
            client_id="client-id",
            scope=hf_oauth.HF_OAUTH_SCOPE,
        )
        hf_oauth._device_flows[flow.flow_id] = flow

        def fake_post_form(url: str, data: dict[str, str]) -> dict:
            return {"access_token": "hf_oauth_secret", "token_type": "bearer"}

        hf_oauth._post_form = fake_post_form
        hf_oauth.save_hf_token = saved_tokens.append
        hf_oauth._public_connection = lambda token: {
            "configured": True,
            "connected": True,
            "username": "tester",
            "token_preview": f"{token[:8]}...",
            "scope": hf_oauth.HF_OAUTH_SCOPE,
            "client_id_set": True,
        }
        try:
            result = hf_oauth.poll_huggingface_device_flow("flow-1")
        finally:
            hf_oauth._post_form = original_post_form
            hf_oauth.save_hf_token = original_save_token
            hf_oauth._public_connection = original_public_connection

        self.assertEqual(result["status"], "connected")
        self.assertEqual(saved_tokens, ["hf_oauth_secret"])
        self.assertNotIn("hf_oauth_secret", json.dumps(result))
        self.assertNotIn("flow-1", hf_oauth._device_flows)

    def test_huggingface_download_uses_saved_token_then_imports_snapshot(self) -> None:
        original_get_token = hf_oauth.get_hf_token
        original_hf_api = hf_oauth.HfApi
        original_snapshot_download = hf_oauth.snapshot_download
        original_import_local_model = hf_oauth.import_local_model
        calls: list[tuple[str, str | None, str | None, list[str]]] = []

        class FakeHfApi:
            def list_repo_files(self, **kwargs) -> list[str]:
                return ["config.json", "model.safetensors", "pytorch_model.bin"]

        def fake_snapshot_download(
            *,
            repo_id: str,
            revision: str | None,
            token: str | None,
            repo_type: str,
            ignore_patterns: list[str],
        ) -> str:
            self.assertEqual(repo_type, "model")
            calls.append((repo_id, revision, token, ignore_patterns))
            return "/tmp/hf-snapshot"

        hf_oauth.get_hf_token = lambda: "hf_oauth_secret"
        hf_oauth.HfApi = FakeHfApi
        hf_oauth.snapshot_download = fake_snapshot_download
        hf_oauth.import_local_model = lambda model_name, path: {
            "model_name": model_name,
            "valid": True,
            "gated": True,
            "weight_file_count": 1,
            "sharded": False,
        }
        try:
            result = hf_oauth.download_huggingface_model("meta-llama/Llama-3.2-1B")
        finally:
            hf_oauth.get_hf_token = original_get_token
            hf_oauth.HfApi = original_hf_api
            hf_oauth.snapshot_download = original_snapshot_download
            hf_oauth.import_local_model = original_import_local_model

        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(calls[0][:3], ("meta-llama/Llama-3.2-1B", None, "hf_oauth_secret"))
        self.assertIn("*.bin", calls[0][3])
        self.assertIn("pytorch_model.bin.index.json", calls[0][3])
        self.assertNotIn("hf_oauth_secret", json.dumps(result))

    def test_huggingface_download_requires_connection_for_gated_model(self) -> None:
        original_get_token = hf_oauth.get_hf_token
        hf_oauth.get_hf_token = lambda: None
        try:
            with self.assertRaises(hf_oauth.HuggingFaceOAuthError) as raised:
                hf_oauth.download_huggingface_model("meta-llama/Llama-3.2-1B")
        finally:
            hf_oauth.get_hf_token = original_get_token

        self.assertEqual(raised.exception.code, "hf_not_connected")
        self.assertEqual(raised.exception.status_code, 401)

    def test_huggingface_download_maps_gated_access_denied(self) -> None:
        original_get_token = hf_oauth.get_hf_token
        original_hf_api = hf_oauth.HfApi
        original_snapshot_download = hf_oauth.snapshot_download

        class FakeHfApi:
            def list_repo_files(self, **kwargs) -> list[str]:
                return ["model.safetensors"]

        class DummyResponse:
            status_code = 403
            headers: dict[str, str] = {}
            request = None

        def fake_snapshot_download(**kwargs) -> str:
            raise hf_oauth.GatedRepoError("not approved", response=DummyResponse())

        hf_oauth.get_hf_token = lambda: "hf_oauth_secret"
        hf_oauth.HfApi = FakeHfApi
        hf_oauth.snapshot_download = fake_snapshot_download
        try:
            with self.assertRaises(hf_oauth.HuggingFaceOAuthError) as raised:
                hf_oauth.download_huggingface_model("meta-llama/Llama-3.2-1B")
        finally:
            hf_oauth.get_hf_token = original_get_token
            hf_oauth.HfApi = original_hf_api
            hf_oauth.snapshot_download = original_snapshot_download

        self.assertEqual(raised.exception.code, "gated_model_access_denied")
        self.assertEqual(raised.exception.status_code, 403)
        self.assertNotIn("hf_oauth_secret", raised.exception.message)

    def test_disconnect_deletes_token_and_clears_device_flows(self) -> None:
        original_delete_token = hf_oauth.delete_hf_token
        deleted: list[bool] = []
        hf_oauth._device_flows["flow-1"] = hf_oauth.DeviceFlow(
            flow_id="flow-1",
            device_code="device-secret",
            user_code="ABCD-EFGH",
            verification_uri="https://huggingface.co/oauth/device",
            verification_uri_complete=None,
            expires_at=time.time() + 300,
            interval=5,
            client_id="client-id",
            scope=hf_oauth.HF_OAUTH_SCOPE,
        )
        hf_oauth.delete_hf_token = lambda: deleted.append(True)
        try:
            result = hf_oauth.disconnect_huggingface()
        finally:
            hf_oauth.delete_hf_token = original_delete_token

        self.assertEqual(result["status"], "disconnected")
        self.assertEqual(deleted, [True])
        self.assertEqual(hf_oauth._device_flows, {})

    def test_download_job_reports_status_without_token_leak(self) -> None:
        original_get_token = hf_oauth.get_hf_token
        original_download = hf_oauth.download_huggingface_model

        hf_oauth.get_hf_token = lambda: "hf_oauth_secret"
        hf_oauth.download_huggingface_model = lambda model_name, revision=None: {
            "status": "downloaded",
            "model": {
                "model_name": model_name,
                "valid": True,
                "gated": True,
                "weight_file_count": 1,
                "sharded": False,
            },
            "message": f"Downloaded and imported {model_name}.",
        }
        try:
            started = hf_oauth.start_huggingface_model_download(
                "meta-llama/Llama-3.2-1B"
            )
            job_id = started["job"]["job_id"]
            deadline = time.time() + 2
            status = hf_oauth.get_huggingface_download_job(job_id)
            while status["job"]["status"] not in {"completed", "failed"} and time.time() < deadline:
                time.sleep(0.01)
                status = hf_oauth.get_huggingface_download_job(job_id)
        finally:
            hf_oauth.get_hf_token = original_get_token
            hf_oauth.download_huggingface_model = original_download

        self.assertEqual(status["job"]["status"], "completed")
        self.assertEqual(status["job"]["model"]["model_name"], "meta-llama/Llama-3.2-1B")
        self.assertNotIn("hf_oauth_secret", json.dumps(status))

    def test_cancel_download_job_sets_cancel_request(self) -> None:
        job_id = "job-1"
        now = "2026-07-10T00:00:00+00:00"
        hf_oauth._download_jobs[job_id] = {
            "job_id": job_id,
            "model_name": "meta-llama/Llama-3.2-1B",
            "revision": None,
            "status": "downloading",
            "message": "Downloading from Hugging Face.",
            "error": None,
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "model": None,
        }

        result = hf_oauth.cancel_huggingface_download_job(job_id)

        self.assertTrue(result["job"]["cancel_requested"])
        self.assertEqual(result["job"]["status"], "downloading")

    def test_token_storage_uses_config_path_or_env_override(self) -> None:
        original_env = hf_settings.os.environ.get(hf_settings._TOKEN_FILE_ENV)
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "secure" / "hf_token"
            hf_settings.os.environ[hf_settings._TOKEN_FILE_ENV] = str(token_path)
            try:
                hf_settings.save_hf_token("hf_oauth_secret")
                self.assertEqual(hf_settings.get_hf_token(), "hf_oauth_secret")
                self.assertEqual(token_path.read_text(encoding="utf-8"), "hf_oauth_secret")
                hf_settings.delete_hf_token()
                self.assertFalse(token_path.exists())
            finally:
                if original_env is None:
                    hf_settings.os.environ.pop(hf_settings._TOKEN_FILE_ENV, None)
                else:
                    hf_settings.os.environ[hf_settings._TOKEN_FILE_ENV] = original_env


class RemoteSequentialRouteTests(unittest.TestCase):
    def make_node(self, layer_start: int, layer_end: int, peer_id: str = "peer") -> dict:
        return {
            "peer_id": peer_id,
            "layer_start": layer_start,
            "layer_end": layer_end,
            "model_name": "facebook/opt-125m",
            "rpc_uid": "uid-123",
        }

    def test_generator_initial_peers_include_matching_local_node_addresses(self) -> None:
        from api import server as api_server

        class FakeNode:
            def __init__(self, model_name: str, dht_prefix: str, running: bool, maddrs: list[str]) -> None:
                self.model_name = model_name
                self.dht_prefix = dht_prefix
                self.running = running
                self.maddrs = maddrs

            def is_running(self) -> bool:
                return self.running

            def get_visible_maddrs(self) -> list[str]:
                return self.maddrs

        original_local_nodes = dict(api_server.local_nodes)
        api_server.local_nodes.clear()
        api_server.local_nodes.update(
            {
                "matching": FakeNode("facebook/opt-125m", "test-prefix", True, ["local-peer"]),
                "wrong-model": FakeNode("facebook/opt-1.3b", "test-prefix", True, ["wrong-model"]),
                "offline": FakeNode("facebook/opt-125m", "test-prefix", False, ["offline"]),
            }
        )
        try:
            peers = api_server._generator_initial_peers(
                ["bootstrap", "bootstrap"],
                "facebook/opt-125m",
                "test-prefix",
            )
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)

        self.assertEqual(peers, ["bootstrap", "local-peer"])

    def test_reachable_route_probes_each_expert(self) -> None:
        from client import sequential as sequential_module

        class FakeExpert:
            @property
            def info(self) -> dict:
                probed.append(True)
                return {"ready": True}

        route = [self.make_node(0, 8, "peer-a")]
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        sequential.validate_route = lambda nodes=None: route
        original_get_experts = sequential_module.get_experts
        probed: list[bool] = []
        sequential_module.get_experts = lambda dht, uids: [FakeExpert()]
        try:
            result = sequential.validate_reachable_route()
        finally:
            sequential_module.get_experts = original_get_experts

        self.assertEqual(result, route)
        self.assertEqual(probed, [True])

    def test_reachable_route_rejects_unreachable_expert(self) -> None:
        from client import sequential as sequential_module

        class FailingExpert:
            @property
            def info(self) -> dict:
                raise RuntimeError("routing: not found")

        route = [self.make_node(0, 8, "peer-a")]
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        sequential.validate_route = lambda nodes=None: route
        original_get_experts = sequential_module.get_experts
        sequential_module.get_experts = lambda dht, uids: [FailingExpert()]
        try:
            with self.assertRaisesRegex(RuntimeError, "Route RPC probe failed.*routing: not found"):
                sequential.validate_reachable_route()
        finally:
            sequential_module.get_experts = original_get_experts

    def test_plan_route_returns_contiguous_non_overlapping_spans(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)

        nodes = [self.make_node(0, 4), self.make_node(4, 8)]
        plan = sequential._plan_route(nodes)

        self.assertEqual([node["layer_start"] for node in plan], [0, 4])
        self.assertEqual([node["layer_end"] for node in plan], [4, 8])

    def test_plan_route_rejects_overlapping_spans(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)

        with self.assertRaisesRegex(ValueError, "not contiguous"):
            sequential._plan_route([self.make_node(0, 5), self.make_node(4, 8)])

    def test_plan_route_load_balances_duplicate_layer_replicas(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        first_replica = {
            **self.make_node(0, 4, "peer-a"),
            "rpc_uid": "test-prefix.0.4",
        }
        second_replica = {
            **self.make_node(0, 4, "peer-b"),
            "rpc_uid": "test-prefix.0.4.1",
        }
        tail = self.make_node(4, 8, "peer-tail")

        first_route = sequential._plan_route([second_replica, tail, first_replica])
        second_route = sequential._plan_route([second_replica, tail, first_replica])

        self.assertEqual([node["peer_id"] for node in first_route], ["peer-a", "peer-tail"])
        self.assertEqual([node["peer_id"] for node in second_route], ["peer-b", "peer-tail"])

    def test_validate_route_raises_for_incomplete_coverage(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        sequential._discover_nodes = lambda: [self.make_node(0, 4), self.make_node(6, 8)]

        with self.assertRaisesRegex(RuntimeError, "Incomplete layer coverage"):
            sequential.validate_route()

    def test_discover_nodes_skips_non_integer_layer_metadata(self) -> None:
        dht = MappingDHT(
            {
                "test-prefix.members": ["bad-peer", "good-peer"],
                "test-prefix.node_info.bad-peer": {
                    "peer_id": "bad-peer",
                    "model_name": "facebook/opt-125m",
                    "layer_start": "zero",
                    "layer_end": 4,
                    "rpc_uid": "test-prefix.0.0",
                },
                "test-prefix.node_info.good-peer": self.make_node(0, 8, "good-peer"),
            }
        )
        sequential = RemoteSequential(
            dht,
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )

        nodes = sequential._discover_nodes()

        self.assertEqual([node["peer_id"] for node in nodes], ["good-peer"])

    def test_discovered_node_running_defaults_to_loaded_rpc_state(self) -> None:
        dht = MappingDHT(
            {
                "test-prefix.members": ["peer"],
                "test-prefix.node_info.peer": {
                    **self.make_node(0, 8, "peer"),
                    "layers_loaded": True,
                    "rpc_running": True,
                },
            }
        )
        sequential = RemoteSequential(
            dht,
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )

        nodes = sequential._discover_nodes()

        self.assertEqual(len(nodes), 1)
        self.assertTrue(nodes[0]["running"])
        self.assertEqual(nodes[0]["maddrs"], [])

    def test_validate_route_ignores_offline_loaded_nodes(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        offline_node = {
            **self.make_node(0, 8, "offline-peer"),
            "layers_loaded": True,
            "rpc_running": False,
            "running": False,
        }
        sequential._discover_nodes = lambda: [offline_node]

        with self.assertRaisesRegex(RuntimeError, "No serving nodes"):
            sequential.validate_route()

    def test_network_status_keeps_offline_node_but_excludes_it_from_coverage(self) -> None:
        dht = MappingDHT(
            {
                "test-prefix.members": ["offline-peer"],
                "test-prefix.node_info.offline-peer": {
                    **self.make_node(0, 8, "offline-peer"),
                    "layers_loaded": True,
                    "rpc_running": False,
                    "running": False,
                },
            }
        )
        sequential = RemoteSequential(
            dht,
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )

        status = sequential.get_network_status()

        self.assertEqual(len(status["nodes"]), 1)
        self.assertEqual(status["covered_layers"], 0)
        self.assertEqual(status["missing_layers"], list(range(8)))

    def test_wrong_model_node_metadata_is_rejected(self) -> None:
        sequential = RemoteSequential(
            DummyDHT(),
            "test-prefix",
            num_layers=8,
            model_name="facebook/opt-125m",
        )
        nodes = [
            {
                **self.make_node(0, 8),
                "model_name": "meta-llama/Llama-3.2-1B",
            }
        ]

        with self.assertRaisesRegex(ValueError, "model_name"):
            sequential.validate_route(nodes)

    def test_negative_layer_ranges_do_not_count_as_valid_coverage(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)

        coverage = sequential._check_coverage([self.make_node(-2, 8)])

        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["covered"], [])
        self.assertEqual(coverage["invalid"], ["peer:-2-8"])

    def test_missing_rpc_uid_is_explicit_runtime_error(self) -> None:
        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        node = self.make_node(0, 8)
        node["rpc_uid"] = ""

        with self.assertRaisesRegex(ValueError, "rpc_uid"):
            sequential.validate_route([node])

    def test_rpc_forward_missing_expert_uses_runtime_error(self) -> None:
        import client.sequential as sequential_module

        sequential = RemoteSequential(DummyDHT(), "test-prefix", num_layers=8)
        original_get_experts = sequential_module.get_experts
        sequential_module.get_experts = lambda dht, uids: []
        try:
            with self.assertRaisesRegex(RuntimeError, "not found"):
                sequential._rpc_forward(
                    rpc_uid="test-prefix.0.0",
                    peer_id="peer",
                    hidden_states=torch.zeros(1, 1, 8),
                )
        finally:
            sequential_module.get_experts = original_get_experts

    def test_rpc_uid_includes_layer_slice_for_uniqueness(self) -> None:
        uid_a = RPCServer.build_rpc_uid("test-prefix", layer_start=0, layer_end=4)
        uid_b = RPCServer.build_rpc_uid("test-prefix", layer_start=4, layer_end=8)
        uid_c = RPCServer.build_rpc_uid(
            "test-prefix",
            layer_start=0,
            layer_end=4,
            uid_suffix=1,
        )

        self.assertNotEqual(uid_a, uid_b)
        self.assertNotEqual(uid_a, uid_c)
        self.assertEqual(uid_a, "test-prefix.0.4")
        self.assertEqual(uid_b, "test-prefix.4.8")
        self.assertEqual(uid_c, "test-prefix.0.4.1")

    def test_rpc_stop_is_bounded_when_hivemind_shutdown_hangs(self) -> None:
        class BlockingServer:
            def shutdown(self) -> None:
                time.sleep(1.0)

        rpc = RPCServer.__new__(RPCServer)
        rpc._server = BlockingServer()
        rpc._running = True
        rpc._lock = __import__("threading").Lock()

        started = time.perf_counter()
        rpc.stop(timeout=0.01)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.5)
        self.assertFalse(rpc.is_running())
        self.assertIsNone(rpc._server)

    def test_node_stop_is_bounded_when_dht_shutdown_hangs(self) -> None:
        class FakeRPC:
            def __init__(self) -> None:
                self.timeout = None

            def stop(self, timeout: float = 5.0) -> None:
                self.timeout = timeout

            def is_running(self) -> bool:
                return False

        class FakeHandler:
            def __init__(self) -> None:
                self.unloaded = False

            def unload(self) -> None:
                self.unloaded = True

            def is_loaded(self) -> bool:
                return False

        class BlockingDHT:
            peer_id = "peer"

            def shutdown(self) -> None:
                time.sleep(1.0)

        node = Node(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            dht_prefix="test-prefix",
            device="cpu",
        )
        fake_rpc = FakeRPC()
        fake_handler = FakeHandler()
        node.rpc = fake_rpc
        node.handler = fake_handler
        node.dht = BlockingDHT()
        node._running = True

        started = time.perf_counter()
        node.stop(timeout=0.01)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.5)
        self.assertEqual(fake_rpc.timeout, 0.01)
        self.assertTrue(fake_handler.unloaded)
        self.assertIsNone(node.rpc)
        self.assertIsNone(node.handler)
        self.assertIsNone(node.dht)
        self.assertFalse(node.is_running())

    def test_node_turn_off_stops_rpc_but_keeps_loaded_layers(self) -> None:
        class FakeRPC:
            def __init__(self) -> None:
                self.timeout = None
                self.running = True

            def stop(self, timeout: float = 5.0) -> None:
                self.timeout = timeout
                self.running = False

            def is_running(self) -> bool:
                return self.running

            def get_uid(self) -> str:
                return "test-prefix.0.1"

        class FakeHandler:
            def __init__(self) -> None:
                self.unloaded = False

            def unload(self) -> None:
                self.unloaded = True

            def is_loaded(self) -> bool:
                return not self.unloaded

            def get_accounting_snapshot(self) -> dict:
                return {}

        class FakeDHT:
            peer_id = "peer"

            def __init__(self) -> None:
                self.values: dict[str, object] = {}
                self.shutdown_called = False

            def store(self, key: str, value: object, expiration_time: float) -> None:
                self.values[key] = value

            def get(self, key: str, latest: bool = True) -> DummyDHTResult | None:
                value = self.values.get(key)
                return DummyDHTResult(value) if value is not None else None

            def get_visible_maddrs(self) -> list[str]:
                return ["/ip4/127.0.0.1/tcp/1234"]

            def shutdown(self) -> None:
                self.shutdown_called = True

        node = Node(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            dht_prefix="test-prefix",
            device="cpu",
        )
        fake_rpc = FakeRPC()
        fake_handler = FakeHandler()
        fake_dht = FakeDHT()
        node.rpc = fake_rpc
        node.handler = fake_handler
        node.dht = fake_dht
        node._running = True
        node._ensure_announce_thread = lambda: None

        node.turn_off(timeout=0.01)

        self.assertEqual(fake_rpc.timeout, 0.01)
        self.assertFalse(fake_rpc.is_running())
        self.assertTrue(fake_dht.shutdown_called)
        self.assertFalse(fake_handler.unloaded)
        self.assertIs(node.handler, fake_handler)
        self.assertIsNone(node.rpc)
        self.assertIsNone(node.dht)
        self.assertFalse(node.is_running())
        info = fake_dht.values["test-prefix.node_info.peer"]
        self.assertTrue(info["layers_loaded"])
        self.assertFalse(info["rpc_running"])
        self.assertFalse(info["running"])
        status = node.get_info()
        self.assertEqual(status["peer_id"], "peer")
        self.assertEqual(status["maddrs"], ["/ip4/127.0.0.1/tcp/1234"])
        self.assertTrue(status["layers_loaded"])
        self.assertFalse(status["rpc_running"])

    def test_node_start_reuses_loaded_handler_when_resuming(self) -> None:
        import node.node as node_module

        class FakeHandler:
            def __init__(self) -> None:
                self.load_calls = 0

            def load(self) -> None:
                self.load_calls += 1

            def is_loaded(self) -> bool:
                return True

            def get_accounting_snapshot(self) -> dict:
                return {}

        class FakeDHT:
            peer_id = "peer-resumed"

            def __init__(self, *args, **kwargs) -> None:
                self.values: dict[str, object] = {}

            def store(self, key: str, value: object, expiration_time: float) -> None:
                self.values[key] = value

            def get(self, key: str, latest: bool = True) -> DummyDHTResult | None:
                value = self.values.get(key)
                return DummyDHTResult(value) if value is not None else None

            def get_visible_maddrs(self) -> list[str]:
                return ["/ip4/127.0.0.1/tcp/4321"]

        class FakeRPC:
            def __init__(self, handler, dht, dht_prefix, uid_suffix=None) -> None:
                self.handler = handler
                self.running = False

            def start(self) -> None:
                self.running = True

            def is_running(self) -> bool:
                return self.running

            def get_uid(self) -> str:
                return "test-prefix.0.1"

        node = Node(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            dht_prefix="test-prefix",
            device="cpu",
        )
        fake_handler = FakeHandler()
        node.handler = fake_handler
        node._ensure_announce_thread = lambda: None

        original_dht = node_module.hivemind.DHT
        original_rpc = node_module.RPCServer
        node_module.hivemind.DHT = FakeDHT
        node_module.RPCServer = FakeRPC
        try:
            node.start()
        finally:
            node_module.hivemind.DHT = original_dht
            node_module.RPCServer = original_rpc

        self.assertIs(node.handler, fake_handler)
        self.assertEqual(fake_handler.load_calls, 0)
        self.assertTrue(node.is_running())

    def test_get_visible_maddrs_falls_back_when_dht_handle_is_closed(self) -> None:
        class ClosedDHT:
            peer_id = "peer"

            def get_visible_maddrs(self) -> list[str]:
                raise OSError("handle is closed")

        node = Node(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            dht_prefix="test-prefix",
            device="cpu",
        )
        node.dht = ClosedDHT()
        node._last_maddrs = ["/ip4/127.0.0.1/tcp/1234"]

        self.assertEqual(node.get_visible_maddrs(), ["/ip4/127.0.0.1/tcp/1234"])

    def test_nodes_endpoint_uses_active_node_prefix(self) -> None:
        from api import server as api_server

        seen_prefixes: list[str] = []

        class DummyNode:
            dht = object()
            dht_prefix = "custom-prefix"

        class CaptureSequential:
            def __init__(
                self,
                dht,
                dht_prefix: str,
                num_layers: int,
                model_name: str | None = None,
            ):
                seen_prefixes.append(dht_prefix)

            def get_network_status(self) -> dict:
                return {"nodes": []}

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        original_sequential = api_server.RemoteSequential
        api_server.node = DummyNode()
        api_server.client_dht = None
        api_server.RemoteSequential = CaptureSequential
        try:
            asyncio.run(api_server.get_nodes())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht
            api_server.RemoteSequential = original_sequential

        self.assertEqual(seen_prefixes, ["custom-prefix"])

    def test_prepare_hidden_states_adds_position_embeddings(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)

        input_ids = torch.tensor([[1, 2, 3]])
        position_ids = torch.tensor([[0, 1, 2]])

        attention_mask = torch.ones_like(input_ids, dtype=torch.bool)

        hidden = generator._prepare_hidden_states(input_ids, attention_mask, position_ids)

        expected = generator.embed_tokens(input_ids) + generator.position_embeddings(position_ids)
        self.assertTrue(torch.allclose(hidden, expected))

    def test_opt_adapter_matches_huggingface_embedding_reference(self) -> None:
        from transformers import OPTConfig, OPTForCausalLM

        adapter = get_architecture_adapter("facebook/opt-125m")
        model = OPTForCausalLM(
            OPTConfig(
                vocab_size=20,
                hidden_size=8,
                word_embed_proj_dim=8,
                ffn_dim=16,
                num_hidden_layers=1,
                num_attention_heads=2,
                max_position_embeddings=16,
            )
        )

        input_ids = torch.tensor([[1, 2, 3]])
        attention_mask = torch.ones_like(input_ids)
        position_ids = torch.tensor([[3, 4, 5]])

        hidden = adapter.prepare_inputs(
            model,
            input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )

        decoder = model.model.decoder
        inputs_embeds = decoder.embed_tokens(input_ids)
        pos_embeds = decoder.embed_positions(
            attention_mask,
            0,
            position_ids=position_ids,
        )
        expected = inputs_embeds + pos_embeds.to(inputs_embeds.device)
        self.assertTrue(torch.allclose(hidden, expected))

    def test_adapter_selection_rejects_unsupported_architecture(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported model architecture"):
            get_architecture_adapter("unknown/future-model")

    def test_generator_readiness_rejects_missing_components(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())

        with self.assertRaisesRegex(RuntimeError, "missing local component"):
            generator._validate_loaded_components()

    def test_generator_readiness_rejects_hidden_size_mismatch(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())
        generator.tokenizer = object()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator.architecture_adapter = get_architecture_adapter("facebook/opt-125m")
        generator._loaded_model = nn.Module()

        with self.assertRaisesRegex(RuntimeError, "hidden size mismatch"):
            generator._validate_loaded_components()

    def test_generate_stream_initializes_position_ids_for_hidden_state_preparation(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class DummySequential:
            def __init__(self) -> None:
                self.received_position_ids = None

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                self.received_position_ids = position_ids
                return hidden_states, []

        tokenizer = DummyTokenizer()
        sequential = DummySequential()
        generator = DistributedGenerator("facebook/opt-125m", sequential=sequential, device="cpu")
        generator._loaded = True
        generator.tokenizer = tokenizer
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator._sample = lambda logits, **kwargs: torch.tensor([[1]])

        async def run_generation() -> list[dict]:
            return [item async for item in generator.generate_stream("hello", max_new_tokens=1)]

        result = asyncio.run(run_generation())

        self.assertTrue(any(item.get("done") for item in result))
        self.assertIsNotNone(sequential.received_position_ids)
        self.assertEqual(tuple(sequential.received_position_ids.shape), (1, 2))

    def test_generate_stream_updates_position_ids_across_steps(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class DummySequential:
            def __init__(self) -> None:
                self.received_position_ids: list[torch.Tensor] = []

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                self.received_position_ids.append(position_ids.clone())
                return hidden_states, []

        tokenizer = DummyTokenizer()
        sequential = DummySequential()
        generator = DistributedGenerator("facebook/opt-125m", sequential=sequential, device="cpu")
        generator._loaded = True
        generator.tokenizer = tokenizer
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator._sample = lambda logits, **kwargs: torch.tensor([[1]])

        async def run_generation() -> list[dict]:
            return [item async for item in generator.generate_stream("hello", max_new_tokens=2)]

        result = asyncio.run(run_generation())

        self.assertTrue(any(item.get("done") for item in result))
        self.assertEqual(len(sequential.received_position_ids), 2)
        self.assertEqual(tuple(sequential.received_position_ids[0].shape), (1, 2))
        self.assertEqual(tuple(sequential.received_position_ids[1].shape), (1, 3))

    def test_generate_stream_passes_exact_generation_controls_to_sampling(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, []

        received_kwargs: dict[str, object] = {}
        generator = DistributedGenerator(
            "facebook/opt-125m",
            sequential=IdentitySequential(),
            device="cpu",
        )
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)

        def capture_sample(logits: torch.Tensor, **kwargs) -> torch.Tensor:
            received_kwargs.update(kwargs)
            return torch.tensor([[1]])

        generator._sample = capture_sample

        async def run_generation() -> list[dict]:
            return [
                item
                async for item in generator.generate_stream(
                    "hello",
                    max_new_tokens=1,
                    temperature=0.25,
                    top_p=1.0,
                    top_k=0,
                    repetition_penalty=1.0,
                    do_sample=False,
                )
            ]

        result = asyncio.run(run_generation())

        self.assertTrue(any(item.get("done") for item in result))
        self.assertEqual(received_kwargs["temperature"], 0.25)
        self.assertEqual(received_kwargs["top_p"], 1.0)
        self.assertEqual(received_kwargs["top_k"], 0)
        self.assertEqual(received_kwargs["repetition_penalty"], 1.0)
        self.assertFalse(received_kwargs["do_sample"])

    def test_generate_stream_honors_stop_request_after_route_step(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids: torch.Tensor, skip_special_tokens: bool = True) -> str:
                return "x"

        class StopSequential:
            def __init__(self) -> None:
                self.generator: DistributedGenerator | None = None

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                assert self.generator is not None
                self.generator.request_stop()
                return hidden_states, ["peer… (layers 0→1)"]

        sequential = StopSequential()
        generator = DistributedGenerator("facebook/opt-125m", sequential=sequential, device="cpu")
        sequential.generator = generator
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.position_embeddings = nn.Embedding(8, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)

        async def run_generation() -> list[dict]:
            return [item async for item in generator.generate_stream("hello", max_new_tokens=4)]

        result = asyncio.run(run_generation())

        self.assertEqual(result, [{"done": True, "node_trace": ["peer… (layers 0→1)"]}])

    def test_sample_uses_argmax_when_do_sample_is_false(self) -> None:
        generator = DistributedGenerator("facebook/opt-125m", sequential=object())

        result = generator._sample(
            torch.tensor([[0.1, 2.0, 1.5]]),
            temperature=0.1,
            top_p=0.1,
            top_k=1,
            repetition_penalty=1.0,
            do_sample=False,
        )

        self.assertEqual(result.tolist(), [[1]])

    def test_compare_next_token_logits_matches_direct_reference(self) -> None:
        class DummyTokenizer:
            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
                token_id = int(token_ids[0])
                return {0: "<eos>", 1: "a", 2: "b", 3: "c"}.get(token_id, "?")

        class TinyCausalLM(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.embed = nn.Embedding(4, 3)
                self.lm_head = nn.Linear(3, 4, bias=False)

            def forward(
                self,
                input_ids: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
            ):
                logits = self.lm_head(self.embed(input_ids))
                return type("TinyOutput", (), {"logits": logits})

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, ["peer… (layers 0→1)"]

        model = TinyCausalLM()
        generator = DistributedGenerator("facebook/opt-125m", sequential=IdentitySequential())
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = model.embed
        generator.norm = nn.Identity()
        generator.lm_head = model.lm_head
        generator._loaded_model = model
        generator.architecture_adapter = None

        result = generator.compare_next_token_logits("hello")

        self.assertTrue(result["allclose"])
        self.assertTrue(result["argmax_match"])
        self.assertEqual(result["max_abs_diff"], 0.0)
        self.assertEqual(result["mean_abs_diff"], 0.0)
        self.assertEqual(result["node_trace"], ["peer… (layers 0→1)"])

    def test_compare_next_token_endpoint_uses_loaded_generator(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.received_prompt = None
                self.received_atol = None
                self.received_rtol = None

            def is_loaded(self) -> bool:
                return True

            def compare_next_token_logits(
                self,
                prompt: str,
                atol: float,
                rtol: float,
            ) -> dict:
                self.received_prompt = prompt
                self.received_atol = atol
                self.received_rtol = rtol
                return {
                    "prompt": prompt,
                    "argmax_match": True,
                    "allclose": True,
                }

        dummy = DummyGenerator()
        original_generator = api_server.generator
        api_server.generator = dummy
        try:
            request = api_server.NextTokenParityRequest(
                prompt="The capital of France is",
                atol=0.01,
                rtol=0.02,
            )
            result = asyncio.run(api_server.compare_generator_next_token(request))
        finally:
            api_server.generator = original_generator

        self.assertEqual(result["prompt"], "The capital of France is")
        self.assertTrue(result["allclose"])
        self.assertEqual(dummy.received_prompt, "The capital of France is")
        self.assertEqual(dummy.received_atol, 0.01)
        self.assertEqual(dummy.received_rtol, 0.02)

    def test_compare_generated_output_matches_direct_reference_greedy(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0
            pad_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
                if isinstance(token_ids, torch.Tensor):
                    token_ids = token_ids.tolist()
                return "".join(
                    {0: "", 1: "a", 2: "b", 3: "c"}.get(int(token_id), "?")
                    for token_id in token_ids
                )

        class TinyGenerateModel(nn.Module):
            def to(self, device):
                return self

            def eval(self):
                return self

            def generate(self, **kwargs):
                return torch.tensor([[1, 2, 3]])

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, ["peer… (layers 0→1)"]

        generator = DistributedGenerator("facebook/opt-125m", sequential=IdentitySequential())
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(10, 4)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(4, 10)
        generator._loaded_model = TinyGenerateModel()
        generator.architecture_adapter = None
        generator._sample = lambda logits, **kwargs: torch.tensor([[3]])

        result = asyncio.run(
            generator.compare_generated_output(
                "hello",
                max_new_tokens=1,
                repetition_penalty=1.0,
                do_sample=False,
            )
        )

        self.assertEqual(result["direct_response"], "c")
        self.assertEqual(result["distributed_response"], "c")
        self.assertTrue(result["exact_text_match"])
        self.assertFalse(result["generation_config"]["do_sample"])
        self.assertEqual(result["node_trace"], ["peer… (layers 0→1)"])

    def test_compare_generated_output_endpoint_uses_loaded_generator(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.received: dict[str, object] = {}

            def is_loaded(self) -> bool:
                return True

            async def compare_generated_output(
                self,
                prompt: str,
                max_new_tokens: int | None,
                temperature: float | None,
                top_p: float | None,
                top_k: int | None,
                repetition_penalty: float | None,
                do_sample: bool | None,
            ) -> dict:
                self.received = {
                    "prompt": prompt,
                    "max_new_tokens": max_new_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": top_k,
                    "repetition_penalty": repetition_penalty,
                    "do_sample": do_sample,
                }
                return {
                    "prompt": prompt,
                    "direct_response": "c",
                    "distributed_response": "c",
                    "exact_text_match": True,
                }

        dummy = DummyGenerator()
        original_generator = api_server.generator
        api_server.generator = dummy
        try:
            request = api_server.GeneratedParityRequest(
                prompt="hello",
                max_new_tokens=3,
                temperature=0.2,
                top_p=1.0,
                top_k=0,
                repetition_penalty=1.0,
                do_sample=False,
            )
            result = asyncio.run(api_server.compare_generator_output(request))
        finally:
            api_server.generator = original_generator

        self.assertTrue(result["exact_text_match"])
        self.assertEqual(dummy.received["prompt"], "hello")
        self.assertEqual(dummy.received["max_new_tokens"], 3)
        self.assertEqual(dummy.received["top_k"], 0)
        self.assertEqual(dummy.received["repetition_penalty"], 1.0)
        self.assertFalse(dummy.received["do_sample"])

    def test_trace_generation_records_token_steps(self) -> None:
        class DummyTokenizer:
            eos_token_id = 0

            def encode(self, prompt: str, return_tensors: str = "pt") -> torch.Tensor:
                return torch.tensor([[1, 2]])

            def decode(self, token_ids, skip_special_tokens: bool = True) -> str:
                token_id = int(token_ids[0])
                return {0: "<eos>", 1: "a", 2: "b", 3: "c"}.get(token_id, "?")

        class IdentitySequential:
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> tuple[torch.Tensor, list[str]]:
                return hidden_states, ["peer… (layers 0→1)"]

        generator = DistributedGenerator("facebook/opt-125m", sequential=IdentitySequential())
        generator._loaded = True
        generator.tokenizer = DummyTokenizer()
        generator.embed_tokens = nn.Embedding(4, 3)
        generator.norm = nn.Identity()
        generator.lm_head = nn.Linear(3, 4, bias=False)
        generator.architecture_adapter = None

        result = generator.trace_generation("hello", max_new_tokens=1)

        self.assertEqual(result["prompt_token_ids"], [1, 2])
        self.assertTrue(result["generation_config"]["do_sample"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertIn("token_id", result["steps"][0])
        self.assertIn("decoded_output_so_far", result["steps"][0])
        self.assertIn("top_candidates", result["steps"][0])
        self.assertEqual(result["node_trace"], ["peer… (layers 0→1)"])

    def test_trace_generation_endpoint_uses_loaded_generator(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.received_prompt = None
                self.received_max_new_tokens = None

            def is_loaded(self) -> bool:
                return True

            def trace_generation(
                self,
                prompt: str,
                max_new_tokens: int | None,
                temperature: float | None,
                top_p: float | None,
                top_k: int | None,
                repetition_penalty: float | None,
                do_sample: bool | None,
            ) -> dict:
                self.received_prompt = prompt
                self.received_max_new_tokens = max_new_tokens
                return {
                    "prompt": prompt,
                    "steps": [],
                    "generation_config": {
                        "top_k": top_k,
                        "repetition_penalty": repetition_penalty,
                        "do_sample": do_sample,
                    },
                }

        dummy = DummyGenerator()
        original_generator = api_server.generator
        original_trace_dir = api_server.TRACE_DIR
        api_server.generator = dummy
        with tempfile.TemporaryDirectory() as trace_dir:
            api_server.TRACE_DIR = Path(trace_dir)
            try:
                request = api_server.GenerationTraceRequest(
                    prompt="test",
                    max_new_tokens=4,
                    top_k=0,
                    repetition_penalty=1.0,
                    do_sample=False,
                )
                result = asyncio.run(api_server.trace_generator(request))
            finally:
                api_server.generator = original_generator
                api_server.TRACE_DIR = original_trace_dir

            self.assertEqual(result["prompt"], "test")
            self.assertEqual(dummy.received_prompt, "test")
            self.assertEqual(dummy.received_max_new_tokens, 4)
            self.assertEqual(result["generation_config"]["top_k"], 0)
            self.assertEqual(result["generation_config"]["repetition_penalty"], 1.0)
            self.assertFalse(result["generation_config"]["do_sample"])
            self.assertIn("trace_id", result)
            self.assertIn("trace_file", result)
            trace_document = json.loads(Path(result["trace_file"]).read_text(encoding="utf-8"))
            self.assertEqual(trace_document["trace_id"], result["trace_id"])
            self.assertEqual(trace_document["trace"]["prompt"], "test")

    def test_trace_analysis_groups_saved_artifacts_and_flags_discrepancies(self) -> None:
        from api import server as api_server

        def trace_document(
            trace_id: str,
            token_id: int,
            response: str,
            node_trace: list[str],
            top_candidate_ids: list[int] | None = None,
            selected_in_top_candidates: bool = True,
            response_contains_replacement_char: bool = False,
        ) -> dict:
            candidate_ids = top_candidate_ids or [3, 4, 5]
            return {
                "schema_version": 1,
                "trace_id": trace_id,
                "created_at": "2026-07-09T00:00:00+00:00",
                "trace": {
                    "prompt": "hello",
                    "model_name": "facebook/opt-1.3b",
                    "generation_config": {
                        "max_new_tokens": 1,
                        "temperature": 0.0,
                        "top_p": 1.0,
                        "top_k": 5,
                        "repetition_penalty": 1.0,
                        "do_sample": False,
                    },
                    "prompt_token_ids": [10, 11],
                    "prompt_tokens": [
                        {
                            "token_id": 10,
                            "token_text": "he",
                            "token_text_contains_replacement_char": False,
                        },
                        {
                            "token_id": 11,
                            "token_text": "llo",
                            "token_text_contains_replacement_char": False,
                        },
                    ],
                    "response": response,
                    "response_contains_replacement_char": response_contains_replacement_char,
                    "steps": [
                        {
                            "step": 0,
                            "hidden_shape_before_route": [1, 2, 2048],
                            "hidden_shape_after_route": [1, 2, 2048],
                            "token_id": token_id,
                            "token_text": response,
                            "token_text_contains_replacement_char": (
                                response_contains_replacement_char
                            ),
                            "decoded_output_so_far": response,
                            "decoded_output_contains_replacement_char": (
                                response_contains_replacement_char
                            ),
                            "selected_in_top_candidates": selected_in_top_candidates,
                            "top_candidates": [
                                {
                                    "token_id": candidate_id,
                                    "token_text": str(candidate_id),
                                    "token_text_contains_replacement_char": False,
                                    "logit": float(index),
                                }
                                for index, candidate_id in enumerate(candidate_ids)
                            ],
                        }
                    ],
                    "node_trace": node_trace,
                },
            }

        with tempfile.TemporaryDirectory() as trace_dir:
            trace_path = Path(trace_dir)
            (trace_path / "baseline.json").write_text(
                json.dumps(
                    trace_document("baseline", 3, "a", ["peer-a… (layers 0→24)"])
                ),
                encoding="utf-8",
            )
            (trace_path / "candidate.json").write_text(
                json.dumps(
                    trace_document(
                        "candidate",
                        8,
                        "\ufffd",
                        ["peer-b… (layers 0→12)", "peer-c… (layers 12→24)"],
                        top_candidate_ids=[6, 7, 9],
                        selected_in_top_candidates=False,
                        response_contains_replacement_char=True,
                    )
                ),
                encoding="utf-8",
            )

            analysis = api_server._analyze_generation_traces(trace_path)

        self.assertEqual(analysis["trace_count"], 2)
        self.assertEqual(analysis["error_count"], 0)
        self.assertEqual(len(analysis["groups"]), 1)
        self.assertEqual(analysis["discrepancy_counts"]["cross_run"], 1)
        self.assertEqual(analysis["discrepancy_counts"]["replacement_char"], 1)
        self.assertEqual(
            analysis["discrepancy_counts"]["selected_outside_top_candidates"],
            1,
        )
        categories = analysis["discrepancies"]["cross_run"][0]["categories"]
        self.assertIn("selected_token_mismatch", categories)
        self.assertIn("top_candidate_mismatch", categories)
        self.assertIn("decoded_output_mismatch", categories)
        self.assertIn("route_shape_mismatch", categories)
        self.assertEqual(
            analysis["discrepancies"]["replacement_char_trace_ids"],
            ["candidate"],
        )

    def test_trace_analysis_endpoint_filters_by_supported_model(self) -> None:
        from api import server as api_server

        original_trace_dir = api_server.TRACE_DIR
        with tempfile.TemporaryDirectory() as trace_dir:
            api_server.TRACE_DIR = Path(trace_dir)
            (api_server.TRACE_DIR / "opt.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "trace_id": "opt",
                        "created_at": "2026-07-09T00:00:00+00:00",
                        "trace": {
                            "model_name": "facebook/opt-125m",
                            "prompt": "hello",
                            "generation_config": {},
                            "prompt_token_ids": [],
                            "prompt_tokens": [],
                            "steps": [],
                            "response": "",
                            "response_contains_replacement_char": False,
                            "node_trace": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            try:
                result = asyncio.run(
                    api_server.analyze_generator_traces(model_name="facebook/opt-1.3b")
                )
                with self.assertRaisesRegex(api_server.HTTPException, "Unsupported model"):
                    asyncio.run(api_server.analyze_generator_traces(model_name="missing"))
            finally:
                api_server.TRACE_DIR = original_trace_dir

        self.assertEqual(result["trace_count"], 0)
        self.assertEqual(result["model_name"], "facebook/opt-1.3b")

    def test_generator_status_reports_not_loaded(self) -> None:
        from api import server as api_server

        original_generator = api_server.generator
        api_server.generator = None
        try:
            result = asyncio.run(api_server.get_generator_status())
        finally:
            api_server.generator = original_generator

        self.assertFalse(result["ready"])
        self.assertEqual(result["reasons"], ["Generator not loaded."])

    def test_generator_status_reports_route_validation_error(self) -> None:
        from api import server as api_server

        class DummySequential:
            def validate_reachable_route(self) -> list[dict]:
                raise RuntimeError("missing layers")

        class DummyGenerator:
            model_name = "facebook/opt-125m"
            sequential = DummySequential()

            def is_loaded(self) -> bool:
                return True

        original_generator = api_server.generator
        api_server.generator = DummyGenerator()
        try:
            result = asyncio.run(api_server.get_generator_status())
        finally:
            api_server.generator = original_generator

        self.assertFalse(result["ready"])
        self.assertFalse(result["route_ready"])
        self.assertEqual(result["reasons"], ["missing layers"])

    def test_models_report_not_runnable_without_dht_connection(self) -> None:
        from api import server as api_server

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        api_server.node = None
        api_server.client_dht = None
        try:
            result = asyncio.run(api_server.get_models())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht

        opt = next(model for model in result["models"] if model["id"] == "facebook/opt-125m")
        self.assertFalse(opt["runnable"])
        self.assertFalse(opt["route_ready"])
        self.assertEqual(opt["covered_layers"], 0)
        self.assertEqual(opt["missing_layers"], list(range(12)))
        self.assertEqual(opt["route_reasons"], ["No DHT connection yet."])

    def test_supported_models_include_sprint_11_tuning_metadata(self) -> None:
        expected = {
            "TinyLlama/TinyLlama-1.1B-Chat-v1.0": ("chat", False, 22, 2048),
            "meta-llama/Llama-2-7b-hf": ("base", True, 32, 4096),
            "meta-llama/Llama-2-7b-chat-hf": ("chat", True, 32, 4096),
            "meta-llama/Llama-2-13b-chat-hf": ("chat", True, 40, 5120),
        }

        for model_id, (tuning, gated, num_layers, hidden_size) in expected.items():
            with self.subTest(model_id=model_id):
                metadata = SUPPORTED_MODELS[model_id]
                self.assertEqual(metadata["tuning"], tuning)
                self.assertEqual(metadata["gated"], gated)
                self.assertEqual(metadata["num_layers"], num_layers)
                self.assertEqual(metadata["hidden_size"], hidden_size)

        for model_id, metadata in SUPPORTED_MODELS.items():
            with self.subTest(model_id=model_id):
                self.assertIn(metadata["tuning"], {"base", "chat", "instruct"})
                self.assertGreater(metadata["num_layers"], 0)
                self.assertGreater(metadata["hidden_size"], 0)

    def test_models_endpoint_exposes_tuning_label(self) -> None:
        from api import server as api_server

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        api_server.node = None
        api_server.client_dht = None
        try:
            result = asyncio.run(api_server.get_models())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht

        tiny_llama = next(
            model
            for model in result["models"]
            if model["id"] == "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
        )
        llama2_chat = next(
            model
            for model in result["models"]
            if model["id"] == "meta-llama/Llama-2-7b-chat-hf"
        )
        self.assertEqual(tiny_llama["tuning"], "chat")
        self.assertFalse(tiny_llama["gated"])
        self.assertEqual(llama2_chat["tuning"], "chat")
        self.assertTrue(llama2_chat["gated"])

    def test_models_report_runnable_for_complete_compatible_route(self) -> None:
        from api import server as api_server

        class DummyNode:
            dht = object()
            dht_prefix = "custom-prefix"

        class CaptureSequential:
            def __init__(
                self,
                dht,
                dht_prefix: str,
                num_layers: int,
                model_name: str | None = None,
            ) -> None:
                self.num_layers = num_layers
                self.model_name = model_name

            def get_network_status(self) -> dict:
                if self.model_name == "facebook/opt-125m":
                    return {
                        "nodes": [
                            {
                                "peer_id": "peer-123456",
                                "layer_start": 0,
                                "layer_end": 12,
                                "model_name": "facebook/opt-125m",
                                "rpc_uid": "uid",
                            }
                        ],
                        "covered_layers": 12,
                        "missing_layers": [],
                    }
                return {
                    "nodes": [],
                    "covered_layers": 0,
                    "missing_layers": list(range(self.num_layers)),
                }

            def validate_route(self, nodes: list[dict] | None = None) -> list[dict]:
                if self.model_name != "facebook/opt-125m":
                    raise RuntimeError("No compatible route")
                assert nodes is not None
                return nodes

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        original_sequential = api_server.RemoteSequential
        api_server.node = DummyNode()
        api_server.client_dht = None
        api_server.RemoteSequential = CaptureSequential
        try:
            result = asyncio.run(api_server.get_models())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht
            api_server.RemoteSequential = original_sequential

        opt = next(model for model in result["models"] if model["id"] == "facebook/opt-125m")
        self.assertTrue(opt["runnable"])
        self.assertTrue(opt["route_ready"])
        self.assertEqual(opt["covered_layers"], 12)
        self.assertEqual(opt["missing_layers"], [])
        self.assertEqual(opt["compatible_nodes"], 1)
        self.assertEqual(opt["route_trace"], ["peer-123… (layers 0→12)"])

    def test_token_validation_requires_token_for_gated_model(self) -> None:
        from api import server as api_server

        request = api_server.TokenValidationRequest(
            model_name="meta-llama/Llama-3.2-1B",
        )

        original_get_hf_token = api_server.get_hf_token
        api_server.get_hf_token = lambda: None
        try:
            result = asyncio.run(api_server.validate_token(request))
        finally:
            api_server.get_hf_token = original_get_hf_token

        self.assertFalse(result["valid"])
        self.assertEqual(result["error"], "gated_model_no_token")
        self.assertFalse(result["access_granted"])
        self.assertNotIn("hf_", json.dumps(result))

    def test_token_validation_checks_hub_identity_and_model_access(self) -> None:
        from api import server as api_server

        calls: list[tuple[str, str]] = []

        class FakeHfApi:
            def whoami(self, token: str, cache: bool = False) -> dict:
                calls.append(("whoami", token))
                return {"name": "tester"}

            def model_info(
                self,
                repo_id: str,
                *,
                token: str,
                timeout: int,
            ) -> dict:
                calls.append(("model_info", f"{repo_id}:{token}:{timeout}"))
                return {}

        original_hf_api = api_server.HfApi
        api_server.HfApi = FakeHfApi
        try:
            request = api_server.TokenValidationRequest(
                model_name="meta-llama/Llama-3.2-1B",
                token="hf_valid_token",
            )
            result = asyncio.run(api_server.validate_token(request))
        finally:
            api_server.HfApi = original_hf_api

        self.assertTrue(result["valid"])
        self.assertTrue(result["access_granted"])
        self.assertEqual(
            calls,
            [
                ("whoami", "hf_valid_token"),
                ("model_info", "meta-llama/Llama-3.2-1B:hf_valid_token:10"),
            ],
        )
        self.assertNotIn("hf_valid_token", json.dumps(result))

    def test_start_node_requires_local_import_for_gated_model_before_loading(self) -> None:
        from api import server as api_server

        created: list[object] = []

        class DummyNode:
            def __init__(self, *args, **kwargs) -> None:
                created.append(self)

        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        original_node_class = api_server.Node
        original_get_hf_token = api_server.get_hf_token
        original_get_local_model_path = api_server.get_local_model_path
        api_server.node = None
        api_server.local_nodes.clear()
        api_server.Node = DummyNode
        api_server.get_hf_token = lambda: "hf_denied"
        api_server.get_local_model_path = lambda model_name: None
        try:
            result = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="meta-llama/Llama-3.2-1B",
                        layer_start=0,
                        layer_end=1,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.Node = original_node_class
            api_server.get_hf_token = original_get_hf_token
            api_server.get_local_model_path = original_get_local_model_path
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "local_model_import_required")
        self.assertIn("import the local model directory", result["message"])
        self.assertEqual(created, [])

    def test_public_node_start_does_not_read_or_pass_huggingface_token(self) -> None:
        from api import server as api_server

        captured: dict[str, object] = {}

        class FakeNode:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)
                self.node_id = "public-node"
                self.dht_prefix = kwargs["dht_prefix"]
                self.model_name = kwargs["model_name"]
                self.layer_start = kwargs["layer_start"]
                self.layer_end = kwargs["layer_end"]

            def start(self) -> None:
                pass

            def is_running(self) -> bool:
                return True

            def get_info(self) -> dict:
                return {"node_id": self.node_id, "running": True}

        original_node_class = api_server.Node
        original_get_token = api_server.get_hf_token
        original_get_path = api_server.get_local_model_path
        original_local_nodes = dict(api_server.local_nodes)
        original_node = api_server.node
        api_server.Node = FakeNode
        api_server.get_hf_token = lambda: (_ for _ in ()).throw(
            AssertionError("public startup must not read OAuth state")
        )
        api_server.get_local_model_path = lambda model_name: None
        api_server.local_nodes.clear()
        try:
            result = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                        layer_start=0,
                        layer_end=1,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.Node = original_node_class
            api_server.get_hf_token = original_get_token
            api_server.get_local_model_path = original_get_path
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(result["status"], "started")
        self.assertIsNone(captured["hf_token"])

    def test_failed_node_start_cleans_up_and_maps_expired_oauth(self) -> None:
        from api import server as api_server

        captured: dict[str, object] = {}

        class FailingNode:
            def __init__(self, **kwargs) -> None:
                captured["node"] = self
                self.stopped = False

            def start(self) -> None:
                raise RuntimeError('OAuth token has expired: "exp" claim timestamp check failed')

            def stop(self) -> None:
                self.stopped = True

        original_node_class = api_server.Node
        original_get_path = api_server.get_local_model_path
        original_local_nodes = dict(api_server.local_nodes)
        api_server.Node = FailingNode
        api_server.get_local_model_path = lambda model_name: None
        api_server.local_nodes.clear()
        try:
            result = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                        layer_start=0,
                        layer_end=1,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.Node = original_node_class
            api_server.get_local_model_path = original_get_path
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)

        self.assertEqual(result["error"], "huggingface_reconnect_required")
        self.assertIn("Reconnect Hugging Face", result["message"])
        self.assertTrue(captured["node"].stopped)

    def test_start_generator_requires_local_import_for_gated_model_before_loading(self) -> None:
        from api import server as api_server

        original_generator = api_server.generator
        original_client_dht = api_server.client_dht
        original_get_hf_token = api_server.get_hf_token
        original_get_local_model_path = api_server.get_local_model_path
        api_server.generator = None
        api_server.client_dht = None
        api_server.get_hf_token = lambda: "hf_bad"
        api_server.get_local_model_path = lambda model_name: None
        try:
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(
                        model_name="meta-llama/Llama-3.2-1B",
                        dht_prefix="test-prefix",
                        initial_peers=[],
                    )
                )
            )
        finally:
            api_server.generator = original_generator
            api_server.client_dht = original_client_dht
            api_server.get_hf_token = original_get_hf_token
            api_server.get_local_model_path = original_get_local_model_path

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "local_model_import_required")
        self.assertIn("import the local model directory", result["message"])
        self.assertIsNone(api_server.client_dht)

    def test_start_node_maps_cuda_oom_to_actionable_error(self) -> None:
        from api import server as api_server

        class FailingNode:
            def __init__(self, **kwargs) -> None:
                self.model_name = kwargs["model_name"]
                self.layer_start = kwargs["layer_start"]
                self.layer_end = kwargs["layer_end"]

            def start(self) -> None:
                raise RuntimeError("CUDA error: out of memory cudaErrorMemoryAllocation")

        original_node_class = api_server.Node
        original_local_nodes = dict(api_server.local_nodes)
        api_server.Node = FailingNode
        api_server.local_nodes.clear()
        try:
            result = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                        layer_start=0,
                        layer_end=4,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cuda",
                    )
                )
            )
        finally:
            api_server.Node = original_node_class
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "cuda_out_of_memory")
        self.assertIn("Reduce the served layer range", result["message"])
        self.assertIn("layers 0-4", result["message"])

    def test_start_generator_maps_cuda_oom_to_actionable_error(self) -> None:
        from api import server as api_server

        class FakeDHT:
            peer_id = "fake-client-peer"

            def __init__(self, *args, **kwargs) -> None:
                pass

            def shutdown(self) -> None:
                pass

        class FakeSequential:
            def __init__(self, **kwargs) -> None:
                pass

        class FailingGenerator:
            def __init__(self, **kwargs) -> None:
                self.model_name = kwargs["model_name"]

            def load(self) -> None:
                raise RuntimeError("CUDA error: out of memory")

        original_generator_cls = api_server.DistributedGenerator
        original_dht_cls = api_server.hivemind.DHT
        original_sequential_cls = api_server.RemoteSequential
        original_generator = api_server.generator
        original_client_dht = api_server.client_dht
        api_server.DistributedGenerator = FailingGenerator
        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.generator = None
        api_server.client_dht = None
        try:
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(
                        model_name="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                        dht_prefix="test-prefix",
                        initial_peers=[],
                    )
                )
            )
        finally:
            api_server.DistributedGenerator = original_generator_cls
            api_server.hivemind.DHT = original_dht_cls
            api_server.RemoteSequential = original_sequential_cls
            api_server.generator = original_generator
            api_server.client_dht = original_client_dht

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "cuda_out_of_memory")
        self.assertIn("Reduce the served layer range", result["message"])
        self.assertIsNone(api_server.client_dht)

    def test_failed_generator_start_unloads_partial_state_and_dht(self) -> None:
        from api import server as api_server

        captured: dict[str, object] = {}

        class FakeDHT:
            peer_id = "fake-client-peer"

            def __init__(self, *args, **kwargs) -> None:
                captured["dht"] = self
                self.shutdown_called = False

            def shutdown(self) -> None:
                self.shutdown_called = True

        class FakeSequential:
            def __init__(self, **kwargs) -> None:
                pass

        class FailingGenerator:
            def __init__(self, **kwargs) -> None:
                captured["generator"] = self
                self.unloaded = False

            def load(self) -> None:
                raise RuntimeError("generator load failed")

            def unload(self) -> None:
                self.unloaded = True

        original_generator_cls = api_server.DistributedGenerator
        original_dht_cls = api_server.hivemind.DHT
        original_sequential_cls = api_server.RemoteSequential
        original_get_path = api_server.get_local_model_path
        original_generator = api_server.generator
        original_client_dht = api_server.client_dht
        api_server.DistributedGenerator = FailingGenerator
        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.get_local_model_path = lambda model_name: None
        api_server.generator = None
        api_server.client_dht = None
        try:
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(
                        model_name="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
                        dht_prefix="test-prefix",
                        initial_peers=[],
                    )
                )
            )
        finally:
            api_server.DistributedGenerator = original_generator_cls
            api_server.hivemind.DHT = original_dht_cls
            api_server.RemoteSequential = original_sequential_cls
            api_server.get_local_model_path = original_get_path
            api_server.generator = original_generator
            api_server.client_dht = original_client_dht

        self.assertEqual(result["status"], "error")
        self.assertTrue(captured["generator"].unloaded)
        self.assertTrue(captured["dht"].shutdown_called)

    def test_stop_generator_requests_cancellation(self) -> None:
        from api import server as api_server

        class DummyGenerator:
            def __init__(self) -> None:
                self.stop_requested = False

            def is_loaded(self) -> bool:
                return True

            def request_stop(self) -> None:
                self.stop_requested = True

        dummy = DummyGenerator()
        original_generator = api_server.generator
        api_server.generator = dummy
        try:
            result = asyncio.run(api_server.stop_generator())
        finally:
            api_server.generator = original_generator

        self.assertEqual(result["status"], "stop_requested")
        self.assertTrue(dummy.stop_requested)

    def test_turn_off_node_preserves_global_node(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self) -> None:
                self.turned_off = False

            def is_running(self) -> bool:
                return not self.turned_off

            def turn_off(self) -> None:
                self.turned_off = True

            def get_info(self) -> dict:
                return {"running": self.is_running(), "layers_loaded": True}

        dummy = DummyNode()
        original_node = api_server.node
        api_server.node = dummy
        try:
            result = asyncio.run(api_server.turn_off_node())
        finally:
            api_server.node = original_node

        self.assertEqual(result["status"], "turned_off")
        self.assertTrue(dummy.turned_off)
        self.assertIs(result["info"]["layers_loaded"], True)

    def test_start_node_allows_duplicate_and_multi_model_local_nodes(self) -> None:
        from api import server as api_server

        created: list[object] = []

        class DummyNode:
            def __init__(
                self,
                model_name: str,
                layer_start: int,
                layer_end: int,
                dht_prefix: str,
                initial_peers: list[str],
                device: str,
                hf_token: str | None,
                local_model_path: str | None = None,
            ) -> None:
                self.node_id = f"node-{len(created) + 1}"
                self.model_name = model_name
                self.layer_start = layer_start
                self.layer_end = layer_end
                self.dht_prefix = dht_prefix
                self.initial_peers = initial_peers
                self.device = device
                self.hf_token = hf_token
                self.local_model_path = local_model_path
                self.dht = object()
                self.running = False
                created.append(self)

            def start(self) -> None:
                self.rpc_uid_suffix_at_start = getattr(self, "rpc_uid_suffix", None)
                self.running = True

            def is_running(self) -> bool:
                return self.running

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": self.node_id,
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                    "device": self.device,
                    "running": self.running,
                    "maddrs": [],
                    "layers_loaded": True,
                    "rpc_running": self.running,
                }

        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        original_node_class = api_server.Node
        api_server.node = None
        api_server.local_nodes.clear()
        api_server.Node = DummyNode
        try:
            first = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-125m",
                        layer_start=0,
                        layer_end=6,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
            second = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-125m",
                        layer_start=0,
                        layer_end=6,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
            third = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-1.3b",
                        layer_start=0,
                        layer_end=6,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.Node = original_node_class
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(first["status"], "started")
        self.assertEqual(second["status"], "started")
        self.assertEqual(third["status"], "started")
        self.assertEqual(first["info"]["node_id"], "node-1")
        self.assertEqual(second["info"]["node_id"], "node-2")
        self.assertEqual(third["info"]["node_id"], "node-3")
        self.assertIsNone(created[0].rpc_uid_suffix_at_start)
        self.assertEqual(created[1].rpc_uid_suffix_at_start, 1)
        self.assertIsNone(created[2].rpc_uid_suffix_at_start)

    def test_duplicate_replica_suffix_does_not_reuse_live_suffix_after_delete(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self, node_id: str, rpc_uid_suffix: int | None) -> None:
                self.node_id = node_id
                self.model_name = "facebook/opt-125m"
                self.layer_start = 0
                self.layer_end = 6
                self.dht_prefix = "test-prefix"
                self.rpc_uid_suffix = rpc_uid_suffix

        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        existing_replica = DummyNode("node-2", 1)
        api_server.node = existing_replica
        api_server.local_nodes.clear()
        api_server.local_nodes[existing_replica.node_id] = existing_replica
        try:
            next_suffix = api_server._next_rpc_uid_suffix(
                api_server.NodeStartRequest(
                    model_name="facebook/opt-125m",
                    layer_start=0,
                    layer_end=6,
                    dht_prefix="test-prefix",
                    initial_peers=[],
                    device="cpu",
                )
            )
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(next_suffix, 2)

    def test_start_node_rejects_partial_overlap_for_same_model(self) -> None:
        from api import server as api_server

        class DummyNode:
            node_id = "node-1"
            model_name = "facebook/opt-125m"
            layer_start = 0
            layer_end = 6
            dht_prefix = "test-prefix"
            device = "cpu"

            def is_running(self) -> bool:
                return True

            def get_info(self) -> dict:
                return {"node_id": self.node_id}

        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        dummy = DummyNode()
        api_server.node = dummy
        api_server.local_nodes.clear()
        api_server.local_nodes[dummy.node_id] = dummy
        try:
            overlap = asyncio.run(
                api_server.start_node(
                    api_server.NodeStartRequest(
                        model_name="facebook/opt-125m",
                        layer_start=4,
                        layer_end=8,
                        dht_prefix="test-prefix",
                        initial_peers=[],
                        device="cpu",
                    )
                )
            )
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(overlap["status"], "error")
        self.assertEqual(overlap["error"], "overlapping_layer_range")

    def test_turn_off_node_targets_one_local_node_by_id(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.model_name = "facebook/opt-125m"
                self.layer_start = 0
                self.layer_end = 1
                self.dht_prefix = "test-prefix"
                self.device = "cpu"
                self.turned_off = False

            def is_running(self) -> bool:
                return not self.turned_off

            def turn_off(self) -> None:
                self.turned_off = True

            def get_info(self) -> dict:
                return {"node_id": self.node_id, "running": self.is_running()}

        first = DummyNode("node-1")
        second = DummyNode("node-2")
        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        api_server.local_nodes.clear()
        api_server.local_nodes[first.node_id] = first
        api_server.local_nodes[second.node_id] = second
        api_server.node = first
        try:
            result = asyncio.run(api_server.turn_off_node(node_id="node-1"))
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(result["status"], "turned_off")
        self.assertTrue(first.turned_off)
        self.assertFalse(second.turned_off)

    def test_nodes_endpoint_returns_local_loaded_node_without_active_dht(self) -> None:
        from api import server as api_server

        class DummyNode:
            dht = None

            def get_info(self) -> dict:
                return {
                    "peer_id": "peer",
                    "model_name": "facebook/opt-125m",
                    "layer_start": 0,
                    "layer_end": 1,
                    "device": "cpu",
                    "running": False,
                    "maddrs": [],
                    "layers_loaded": True,
                    "rpc_running": False,
                }

        original_node = api_server.node
        original_client_dht = api_server.client_dht
        api_server.node = DummyNode()
        api_server.client_dht = None
        try:
            result = asyncio.run(api_server.get_nodes())
        finally:
            api_server.node = original_node
            api_server.client_dht = original_client_dht

        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["peer_id"], "peer")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertTrue(result["nodes"][0]["layers_loaded"])

    def test_delete_node_unloads_and_clears_global_node(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        dummy = DummyNode()
        original_node = api_server.node
        api_server.node = dummy
        try:
            result = asyncio.run(api_server.delete_node())
            self.assertIsNone(api_server.node)
        finally:
            api_server.node = original_node

        self.assertEqual(result["status"], "deleted")
        self.assertTrue(dummy.stopped)

    def test_delete_node_targets_one_registered_node_by_id(self) -> None:
        from api import server as api_server

        class DummyNode:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        first = DummyNode("node-1")
        second = DummyNode("node-2")
        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        api_server.local_nodes.clear()
        api_server.local_nodes[first.node_id] = first
        api_server.local_nodes[second.node_id] = second
        api_server.node = first
        try:
            result = asyncio.run(api_server.delete_node(node_id="node-1"))
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertEqual(result["status"], "deleted")
        self.assertTrue(first.stopped)
        self.assertFalse(second.stopped)

    def test_shutdown_local_nodes_returns_promptly_when_node_stop_hangs(self) -> None:
        from api import server as api_server

        class BlockingNode:
            node_id = "blocking-node"

            def stop(self, timeout: float = 5.0) -> None:
                time.sleep(1.0)

        original_node = api_server.node
        original_local_nodes = dict(api_server.local_nodes)
        blocking = BlockingNode()
        api_server.node = blocking
        api_server.local_nodes.clear()
        api_server.local_nodes[blocking.node_id] = blocking
        try:
            started = time.perf_counter()
            result = api_server._shutdown_local_nodes(timeout=0.01)
            elapsed = time.perf_counter() - started
        finally:
            api_server.local_nodes.clear()
            api_server.local_nodes.update(original_local_nodes)
            api_server.node = original_node

        self.assertLess(elapsed, 0.5)
        self.assertEqual(result, [{"node_id": "blocking-node", "status": "timeout"}])
        self.assertIsNone(api_server.node)

    def test_shutdown_client_dht_returns_promptly_when_shutdown_hangs(self) -> None:
        from api import server as api_server

        class BlockingDHT:
            def shutdown(self) -> None:
                time.sleep(1.0)

        original_client_dht = api_server.client_dht
        api_server.client_dht = BlockingDHT()
        try:
            started = time.perf_counter()
            result = api_server._shutdown_client_dht(timeout=0.01)
            elapsed = time.perf_counter() - started
            client_after_shutdown = api_server.client_dht
        finally:
            api_server.client_dht = original_client_dht

        self.assertLess(elapsed, 0.5)
        self.assertEqual(result, {"status": "timeout"})
        self.assertIsNone(client_after_shutdown)
        self.assertIs(api_server.client_dht, original_client_dht)

    def test_incentive_accounting_endpoint_is_simulated_only(self) -> None:
        from api import server as api_server

        class DummyNode:
            def get_accounting_snapshot(self) -> dict:
                return {
                    "peer_id": "peer-123",
                    "model_name": "facebook/opt-125m",
                    "layer_start": 0,
                    "layer_end": 1,
                    "requests_served": 2,
                }

        original_node = api_server.node
        api_server.node = DummyNode()
        try:
            result = asyncio.run(api_server.get_incentive_accounting())
        finally:
            api_server.node = original_node

        self.assertEqual(result["mode"], "simulated")
        self.assertFalse(result["token_ui_enabled"])
        self.assertFalse(result["reward_settlement_enabled"])
        self.assertEqual(result["local_contribution"]["model_name"], "facebook/opt-125m")
        self.assertIn("token_positions_served", result["fields"])

    def test_handler_expands_token_mask_to_causal_decoder_mask(self) -> None:
        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )
        hidden_states = torch.zeros(1, 4, 8)
        token_mask = torch.tensor([[1, 1, 1, 0]], dtype=torch.bool)

        decoder_mask = handler._prepare_decoder_attention_mask(
            token_mask,
            hidden_states,
        )

        self.assertEqual(tuple(decoder_mask.shape), (1, 1, 4, 4))
        self.assertEqual(decoder_mask.dtype, torch.float32)
        self.assertEqual(decoder_mask[0, 0, 2, 0].item(), 0.0)
        self.assertEqual(decoder_mask[0, 0, 2, 2].item(), 0.0)
        self.assertEqual(decoder_mask[0, 0, 2, 3].item(), torch.finfo(torch.float32).min)
        self.assertEqual(decoder_mask[0, 0, 0, 1].item(), torch.finfo(torch.float32).min)

    def test_handler_accounting_tracks_successful_forward(self) -> None:
        class AddOneLayer(nn.Module):
            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> torch.Tensor:
                return hidden_states + 1

        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=2,
            layer_end=3,
            device="cpu",
            dtype=torch.float32,
        )
        handler.layers = nn.ModuleList([AddOneLayer()])
        handler._loaded = True

        output = handler.forward(torch.zeros(1, 4, 8))
        snapshot = handler.get_accounting_snapshot()

        self.assertTrue(torch.allclose(output, torch.ones(1, 4, 8)))
        self.assertEqual(snapshot["model_name"], "facebook/opt-125m")
        self.assertEqual(snapshot["layer_start"], 2)
        self.assertEqual(snapshot["layer_end"], 3)
        self.assertEqual(snapshot["requests_served"], 1)
        self.assertEqual(snapshot["failed_requests"], 0)
        self.assertEqual(snapshot["token_positions_served"], 4)
        self.assertGreaterEqual(snapshot["avg_latency_ms"], 0.0)
        self.assertIsNotNone(snapshot["last_success_at"])

    def test_handler_accounting_tracks_failed_forward(self) -> None:
        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )

        with self.assertRaisesRegex(RuntimeError, "Layers not loaded"):
            handler.forward(torch.zeros(1, 4, 8))

        snapshot = handler.get_accounting_snapshot()
        self.assertEqual(snapshot["requests_served"], 0)
        self.assertEqual(snapshot["failed_requests"], 1)
        self.assertEqual(snapshot["token_positions_served"], 0)
        self.assertIsNotNone(snapshot["last_error_at"])

    def test_handler_forward_passes_layer_ready_attention_mask(self) -> None:
        class CaptureLayer(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.received_attention_mask = None

            def forward(
                self,
                hidden_states: torch.Tensor,
                attention_mask: torch.Tensor | None = None,
                position_ids: torch.Tensor | None = None,
            ) -> torch.Tensor:
                self.received_attention_mask = attention_mask
                return hidden_states + 1

        layer = CaptureLayer()
        handler = InferenceHandler(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )
        handler.layers = nn.ModuleList([layer])
        handler._loaded = True

        output = handler.forward(
            hidden_states=torch.zeros(1, 3, 8),
            attention_mask=torch.ones(1, 3, dtype=torch.bool),
            position_ids=torch.tensor([[0, 1, 2]]),
        )

        self.assertTrue(torch.allclose(output, torch.ones(1, 3, 8)))
        self.assertIsNotNone(layer.received_attention_mask)
        self.assertEqual(tuple(layer.received_attention_mask.shape), (1, 1, 3, 3))

    def test_handler_prepares_llama_position_embeddings(self) -> None:
        from transformers import LlamaConfig

        class LlamaLikeLayer(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.self_attn = nn.Module()
                self.self_attn.config = LlamaConfig(
                    hidden_size=8,
                    intermediate_size=16,
                    num_attention_heads=2,
                    num_key_value_heads=2,
                    num_hidden_layers=1,
                )

        handler = InferenceHandler(
            model_name="meta-llama/Llama-3.2-1B",
            layer_start=0,
            layer_end=1,
            device="cpu",
            dtype=torch.float32,
        )
        handler.layers = nn.ModuleList([LlamaLikeLayer()])
        hidden_states = torch.zeros(1, 3, 8)
        position_ids = torch.tensor([[0, 1, 2]])

        position_embeddings = handler._prepare_position_embeddings(
            hidden_states,
            position_ids,
        )

        self.assertIsNotNone(position_embeddings)
        cos, sin = position_embeddings
        self.assertEqual(tuple(cos.shape), (1, 3, 4))
        self.assertEqual(tuple(sin.shape), (1, 3, 4))


if __name__ == "__main__":
    unittest.main()
