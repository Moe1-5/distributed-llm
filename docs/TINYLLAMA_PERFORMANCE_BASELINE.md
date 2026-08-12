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

## Initial 2026-08-13 Finding

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

## Verified Baseline

A separate fix branch explicitly requests non-dictionary chat-template output and safely accepts mapping-like outputs by extracting `input_ids`. Focused tests use a real Transformers `BatchEncoding`. A fresh system pass then succeeded with this baseline:

| Measurement | Result |
| --- | ---: |
| Node startup | 2916.889 ms |
| Initial route validation | 32.378 ms |
| Generator load | 401.929 ms |
| Time to first token | 1795.227 ms |
| Two-token generation duration | 3116.633 ms |
| Throughput | 0.642 tokens/s |
| Route validation during generation | 0.377 ms |
| Remote calls | 2 |
| Total RPC latency | 2883.702 ms |
| Average RPC latency | 1441.851 ms |
| Last RPC latency | 1253.340 ms |
| Useful positions served | 45 |
| Failed worker requests | 0 |
| Minimum system available RAM | 4.779 GiB |
| Maximum aggregate process-tree RSS | 6.680 GiB |
| End-to-end time including cleanup | 7.535 s |

The chat-template path produced two visible tokens, `The capital`, through one full-range `0-22` peer. All explicit cleanup checks passed, the private JSON file used mode `0600`, and no `p2pd` process remained. That pass still emitted the separately documented destructor-time control-task warning; `feature/remote-expert-p2p-cleanup` subsequently adds explicit cached-replica teardown and verifies a clean process exit in a fresh real split pass.
