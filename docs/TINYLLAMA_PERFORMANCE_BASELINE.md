# TinyLlama Performance Baseline

## Purpose

`backend/tinyllama_performance_probe.py` provides a bounded, reproducible Sprint 14 baseline through the real model loader, Hivemind DHT and expert RPC, distributed generator, chat template, timing metrics, worker accounting, resource sampling, and cleanup paths.

The probe resolves the cached TinyLlama 1.1B Chat snapshot without network access by default. It uses bfloat16 on CPU, one full-range `0-22` serving peer, a separate generator DHT identity, deterministic decoding, and two generated tokens. Evidence output excludes model paths, network addresses, credentials, and private keys.

## Command

From `backend/`:

```bash
uv run --python 3.12 python -m tinyllama_performance_probe \
  --output ~/distribllm-evidence/tinyllama-performance.json
```

Use `--allow-download` only when the public model is not already cached.

## 2026-08-13 Result

The single allowed live pass stopped at the first unexpected behavior:

- cached 2.2 GB bfloat16 weights loaded successfully for the full `0-22` worker;
- the real expert server started;
- the separate client discovered and validated the complete route;
- generator components loaded successfully;
- generation failed before the first RPC with `TypeError: 'str' object cannot be interpreted as an integer`;
- explicit server and DHT cleanup completed, no `p2pd` process remained, and system memory returned to about 5.8 GiB available;
- Hivemind also reproduced the documented late remote-control destructor warning.

Transformers 5.3 defaults `apply_chat_template(..., return_dict=True)`. Production `_encode_prompt` requests PyTorch tensors but assumes the result itself is a tensor; it receives `BatchEncoding`, then `torch.as_tensor` sees the string key `input_ids`. Existing unit doubles return a tensor and therefore did not cover the installed library contract.

Per the system-testing protocol, the defect was documented as Issue 15 without a same-pass source fix or rerun. No performance baseline is claimed, and the Sprint 14 live checkbox remains open until a later fix branch updates the compatibility contract and a fresh live pass succeeds.
