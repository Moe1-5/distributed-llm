"""
Local approved-model import registry.

This module validates Hugging Face model directories that the user downloaded
outside DistribLLM, then stores only local import metadata for startup.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from huggingface_hub import scan_cache_dir
from hivemind.utils.logging import get_logger

from constants import SUPPORTED_MODELS

logger = get_logger(__name__)

_REGISTRY_FILE = Path(__file__).parent.parent / ".local_models.json"


class LocalModelValidationError(ValueError):
    """Raised when a local model directory fails the import contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LocalModelDeletionError(ValueError):
    """Raised when a local model import cannot be removed safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, error_code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalModelValidationError(
            error_code,
            f"{path.name} is not valid JSON: {exc}",
        ) from exc
    except OSError as exc:
        raise LocalModelValidationError(
            error_code,
            f"Could not read {path.name}: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise LocalModelValidationError(error_code, f"{path.name} must contain a JSON object.")
    return payload


def _load_registry() -> dict[str, Any]:
    if not _REGISTRY_FILE.exists():
        return {"schema_version": 1, "imports": {}}
    try:
        payload = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read local model registry %s: %s", _REGISTRY_FILE, exc)
        return {"schema_version": 1, "imports": {}}
    if not isinstance(payload, dict):
        return {"schema_version": 1, "imports": {}}
    imports = payload.get("imports")
    if not isinstance(imports, dict):
        payload["imports"] = {}
    payload.setdefault("schema_version", 1)
    return payload


def _save_registry(payload: dict[str, Any]) -> None:
    _REGISTRY_FILE.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(_REGISTRY_FILE, 0o600)


def _safe_path(path: str) -> Path:
    value = path.strip()
    if not value:
        raise LocalModelValidationError("empty_path", "Local model path must not be empty.")
    lowered = value.lower()
    if (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("huggingface.co/")
    ):
        raise LocalModelValidationError(
            "local_model_url_not_path",
            (
                "This field needs a local folder path, not a Hugging Face URL. "
                "Download the approved model files first, then import the downloaded directory."
            ),
        )
    return Path(value).expanduser().resolve()


def _get_config_layer_count(config: dict[str, Any]) -> int | None:
    for key in ("num_hidden_layers", "n_layer", "num_layers"):
        value = config.get(key)
        if isinstance(value, int):
            return value
    return None


def _get_config_hidden_size(config: dict[str, Any]) -> int | None:
    for key in ("hidden_size", "n_embd", "d_model"):
        value = config.get(key)
        if isinstance(value, int):
            return value
    return None


def _expected_model_type(model_name: str) -> str | None:
    lowered = model_name.lower()
    if "llama" in lowered:
        return "llama"
    if "mistral" in lowered:
        return "mistral"
    if "/opt-" in lowered:
        return "opt"
    return None


def _validate_model_identity(model_name: str, config: dict[str, Any]) -> dict[str, Any]:
    expected = SUPPORTED_MODELS[model_name]
    model_type = str(config.get("model_type", "")).strip().lower()
    expected_type = _expected_model_type(model_name)
    if expected_type is not None and model_type != expected_type:
        raise LocalModelValidationError(
            "wrong_model_architecture",
            (
                f"Expected {model_name} to use model_type {expected_type!r}, "
                f"but config.json reports {model_type or 'missing'}."
            ),
        )

    layer_count = _get_config_layer_count(config)
    if layer_count != int(expected["num_layers"]):
        raise LocalModelValidationError(
            "wrong_model_layer_count",
            (
                f"Expected {model_name} to have {expected['num_layers']} layers, "
                f"but config.json reports {layer_count or 'missing'}."
            ),
        )

    hidden_size = _get_config_hidden_size(config)
    if hidden_size != int(expected["hidden_size"]):
        raise LocalModelValidationError(
            "wrong_model_hidden_size",
            (
                f"Expected {model_name} hidden size {expected['hidden_size']}, "
                f"but config.json reports {hidden_size or 'missing'}."
            ),
        )

    architectures = config.get("architectures", [])
    if not isinstance(architectures, list):
        architectures = []

    return {
        "model_type": model_type,
        "architectures": [str(item) for item in architectures],
        "num_layers": layer_count,
        "hidden_size": hidden_size,
    }


def _validate_tokenizer_files(model_dir: Path) -> list[str]:
    files = {path.name for path in model_dir.iterdir() if path.is_file()}
    if "tokenizer.json" in files:
        return ["tokenizer.json"]
    if "tokenizer.model" in files:
        return ["tokenizer.model"]
    if "vocab.json" in files and "merges.txt" in files:
        return ["vocab.json", "merges.txt"]
    raise LocalModelValidationError(
        "missing_tokenizer_files",
        (
            "Local model directory must include tokenizer.json, tokenizer.model, "
            "or both vocab.json and merges.txt."
        ),
    )


def _index_shards(model_dir: Path, index_name: str) -> tuple[list[str], list[str]]:
    index_path = model_dir / index_name
    if not index_path.exists():
        return [], []

    index = _read_json(index_path, "invalid_weight_index")
    weight_map = index.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        raise LocalModelValidationError(
            "invalid_weight_index",
            f"{index_name} must contain a non-empty weight_map.",
        )

    shard_names = sorted({str(value) for value in weight_map.values()})
    missing = [name for name in shard_names if not (model_dir / name).is_file()]
    return shard_names, missing


def _validate_weight_files(model_dir: Path) -> dict[str, Any]:
    safetensor_shards, missing_safetensors = _index_shards(
        model_dir,
        "model.safetensors.index.json",
    )
    bin_shards, missing_bins = _index_shards(model_dir, "pytorch_model.bin.index.json")

    if missing_safetensors or missing_bins:
        missing = [*missing_safetensors, *missing_bins]
        raise LocalModelValidationError(
            "incomplete_weight_shards",
            f"Weight index references missing shard file(s): {', '.join(missing)}.",
        )

    indexed_shards = safetensor_shards or bin_shards
    if indexed_shards:
        return {
            "weight_format": "safetensors" if safetensor_shards else "bin",
            "weight_files": indexed_shards,
            "weight_file_count": len(indexed_shards),
            "sharded": True,
        }

    weight_files = sorted(
        [
            path.name
            for path in model_dir.iterdir()
            if path.is_file() and path.suffix in {".safetensors", ".bin"}
        ]
    )
    if not weight_files:
        raise LocalModelValidationError(
            "missing_weight_files",
            "Local model directory must include .safetensors or .bin weight files.",
        )

    return {
        "weight_format": "safetensors"
        if any(name.endswith(".safetensors") for name in weight_files)
        else "bin",
        "weight_files": weight_files,
        "weight_file_count": len(weight_files),
        "sharded": False,
    }


def validate_local_model_directory(model_name: str, path: str) -> dict[str, Any]:
    model_name = model_name.strip()
    if model_name not in SUPPORTED_MODELS:
        raise LocalModelValidationError(
            "unsupported_model",
            f"Unsupported model '{model_name}'.",
        )

    model_dir = _safe_path(path)
    if not model_dir.exists():
        raise LocalModelValidationError(
            "path_not_found",
            f"Local model path does not exist: {model_dir}",
        )
    if not model_dir.is_dir():
        raise LocalModelValidationError(
            "path_not_directory",
            f"Local model path is not a directory: {model_dir}",
        )

    config_path = model_dir / "config.json"
    if not config_path.is_file():
        raise LocalModelValidationError(
            "missing_config",
            "Local model directory must include config.json.",
        )

    config = _read_json(config_path, "invalid_config")
    identity = _validate_model_identity(model_name, config)
    tokenizer_files = _validate_tokenizer_files(model_dir)
    weights = _validate_weight_files(model_dir)

    return {
        "valid": True,
        "model_name": model_name,
        "path": str(model_dir),
        "gated": bool(SUPPORTED_MODELS[model_name]["gated"]),
        "validated_at": _now_iso(),
        "config": identity,
        "tokenizer_files": tokenizer_files,
        **weights,
        "message": "Local model directory is valid for offline loading.",
    }


def _public_import(record: dict[str, Any], include_path: bool = False) -> dict[str, Any]:
    result = {
        "model_name": record.get("model_name"),
        "gated": record.get("gated", False),
        "valid": record.get("valid", False),
        "validated_at": record.get("validated_at"),
        "config": record.get("config", {}),
        "tokenizer_files": record.get("tokenizer_files", []),
        "weight_format": record.get("weight_format"),
        "weight_file_count": record.get("weight_file_count", 0),
        "sharded": record.get("sharded", False),
    }
    if include_path:
        result["path"] = record.get("path")
    return result


def _delete_hf_cached_model(model_name: str, path: str | None = None) -> dict[str, Any]:
    snapshot_path = Path(path).expanduser().resolve() if path else None
    try:
        cache_info = scan_cache_dir()
    except Exception as exc:
        raise LocalModelDeletionError(
            "hf_cache_scan_failed",
            f"Could not scan Hugging Face cache before deleting model files: {exc}",
        ) from exc

    for repo in cache_info.repos:
        if repo.repo_type != "model" or repo.repo_id != model_name:
            continue
        revisions = list(repo.revisions)
        managed_paths = {Path(revision.snapshot_path).resolve() for revision in revisions}
        if snapshot_path is not None and snapshot_path not in managed_paths:
            break
        commit_hashes = [revision.commit_hash for revision in revisions]
        if not commit_hashes:
            break
        strategy = cache_info.delete_revisions(*commit_hashes)
        freed_bytes = int(strategy.expected_freed_size)
        freed_size = strategy.expected_freed_size_str
        repo_path = Path(getattr(repo, "repo_path", ""))
        strategy.execute()
        if repo_path.name:
            lock_dir = repo_path.parent / ".locks" / repo_path.name
            try:
                lock_dir.rmdir()
            except OSError:
                pass
        return {
            "files_deleted": True,
            "deleted_bytes": freed_bytes,
            "deleted_size": freed_size,
            "message": (
                f"Deleted all {len(commit_hashes)} cached Hugging Face revision(s) "
                f"for {model_name}."
            ),
        }

    return {
        "files_deleted": False,
        "deleted_bytes": 0,
        "deleted_size": "0.0",
        "message": (
            "Removed the local import registry entry. Files were not deleted because "
            "the path is not a managed Hugging Face cache snapshot."
        ),
    }


def import_local_model(model_name: str, path: str) -> dict[str, Any]:
    validation = validate_local_model_directory(model_name, path)
    registry = _load_registry()
    imports = registry.setdefault("imports", {})
    imports[model_name] = validation
    _save_registry(registry)
    return _public_import(validation)


def inspect_local_model(model_name: str, path: str) -> dict[str, Any]:
    validation = validate_local_model_directory(model_name, path)
    return {
        **_public_import(validation),
        "message": validation.get("message"),
    }


def list_local_models() -> list[dict[str, Any]]:
    registry = _load_registry()
    imports = registry.get("imports", {})
    if not isinstance(imports, dict):
        return []
    return [
        _public_import(record)
        for _, record in sorted(imports.items())
        if isinstance(record, dict)
    ]


def get_local_model_import(model_name: str, include_path: bool = False) -> dict[str, Any] | None:
    registry = _load_registry()
    imports = registry.get("imports", {})
    if not isinstance(imports, dict):
        return None
    record = imports.get(model_name)
    if not isinstance(record, dict):
        return None
    return _public_import(record, include_path=include_path)


def validate_registered_local_model(model_name: str) -> dict[str, Any] | None:
    record = get_local_model_import(model_name, include_path=True)
    if not record or not record.get("valid"):
        return None
    path = record.get("path")
    if not path:
        raise LocalModelValidationError(
            "local_model_import_missing_path",
            f"Stored local import for {model_name} is missing its path. Remove and re-import it.",
        )
    return validate_local_model_directory(model_name, str(path))


def get_local_model_path(model_name: str) -> str | None:
    validation = validate_registered_local_model(model_name)
    if validation is None:
        return None
    return str(validation["path"])


def remove_local_model(model_name: str, delete_files: bool = False) -> dict[str, Any]:
    registry = _load_registry()
    imports = registry.setdefault("imports", {})
    if not isinstance(imports, dict):
        imports = {}
        registry["imports"] = imports
    record = imports.get(model_name)
    registry_removed = model_name in imports

    if not registry_removed and not delete_files:
        return {
            "removed": False,
            "registry_removed": False,
            "files_deleted": False,
            "deleted_bytes": 0,
            "deleted_size": "0.0",
            "message": f"No local import is registered for {model_name}.",
        }

    delete_result = {
        "files_deleted": False,
        "deleted_bytes": 0,
        "deleted_size": "0.0",
        "message": "Removed the local import registry entry.",
    }
    if delete_files:
        if registry_removed and (not isinstance(record, dict) or not record.get("path")):
            raise LocalModelDeletionError(
                "local_model_import_missing_path",
                f"Stored local import for {model_name} is missing its path.",
            )
        registered_path = str(record["path"]) if isinstance(record, dict) else None
        delete_result = _delete_hf_cached_model(model_name, registered_path)

    if registry_removed:
        del imports[model_name]
        _save_registry(registry)
    return {
        "removed": registry_removed or bool(delete_result["files_deleted"]),
        "registry_removed": registry_removed,
        **delete_result,
    }
