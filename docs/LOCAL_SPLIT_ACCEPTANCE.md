# Local Split Acceptance

## Purpose

`backend/local_split_probe.py` proves that the production model-loading, Hivemind DHT, expert RPC, adjacent routing, distributed generation, and accounting paths work across two independent local serving peers. It is a local prerequisite check, not a substitute for the two-physical-device relay test.

The probe uses an isolated loopback bootstrap, two serving DHT identities, and a separate client DHT identity. By default it resolves OPT-125M from the existing Hugging Face cache without network access, serves half-open ranges `0-6` and `6-12`, compares direct and distributed next-token logits, and compares two greedy generated tokens.

## Command

From `backend/`:

```bash
uv run --python 3.12 python -m local_split_probe \
  --output ~/distribllm-evidence/local-split-opt-125m.json
```

Use `--allow-download` only when the model is not already cached. The evidence file is written with mode `0600` and contains public peer IDs, RPC UIDs, ranges, parity metrics, generated output, contribution counters, runtime versions, elapsed time, and cleanup results. It excludes local model paths, DHT addresses, credentials, and private identity material.

Success requires all of the following:

- an exactly adjacent `0-6 -> 6-12` route;
- two distinct serving peer IDs;
- matching Hugging Face and distributed next-token argmax;
- logits within `atol=0.02` and `rtol=0.02`;
- exact greedy generated-text parity;
- both peers recording successful requests and useful positions with no failed requests;
- generator, node, client DHT, and bootstrap shutdown calls completing.

## Recorded Result

The 2026-08-13 CPU pass used Python 3.12.3, Hivemind 1.1.12, PyTorch 2.10.0, and Transformers 5.3.0. It completed in 10.404 seconds.

- The selected route contained two distinct peers serving `0-6` and `6-12`.
- Direct and distributed next-token IDs both equaled `5`; maximum and mean absolute logit differences were `0.0`.
- Direct and distributed two-token greedy output both equaled ` the most`.
- Each worker completed three requests and 19 token positions with zero failed requests.
- All explicit shutdown calls completed and no `p2pd` process remained.

After successful shutdown, the initial pass emitted destructor-time `no current event loop` messages and one pending control-client task. That pass was not patched or rerun in place. A later dedicated cleanup branch now closes Hivemind's DHT-cached replicated P2P control client before DHT shutdown. A fresh split pass retained exact inference/parity, reported `remote_expert_p2p_stopped: true`, left no `p2pd` process, and exited without destructor tracebacks or pending tasks.

The 2026-08-13 responsive-generation pass retained exact OPT-125M parity and measured the ordinary local CPU path. Cold generator component load was 193.557 milliseconds; after unload placed the pruned local component shell in the bounded CPU cache, warm load was 0.196 milliseconds. First-token latency was 84.750 milliseconds, two-token generation completed in 179.568 milliseconds at 11.138 tokens per second, and route validation consumed 0.252 milliseconds across the request. A stop requested during an active route completed in 26.522 milliseconds with zero generated tokens and no second hop. These loopback numbers are a regression baseline, not a prediction of relay or larger-model performance.

## Local Failover Probe

`backend/local_failover_probe.py` is the process-level prerequisite for Sprint 21. It starts two independent full-range Hivemind experts, performs one forward through the selected expert, stops that expert, and requires a second forward to restart from the original activation tensor through the complete replica. It verifies a two-attempt maximum, different accepted peer, output equivalence, no worker-side failed accounting, and complete process cleanup.

From `backend/`:

```bash
uv run --python 3.12 python -m local_failover_probe \
  --output ~/distribllm-evidence/local-failover-opt-125m.json
```

This probe exercises real local expert disappearance and replacement, but it does not prove circuit-relay behavior. Sprint 21 still requires the controlled two-device failure injection through the project VPS relay.

The 2026-08-13 process-level pass used Python 3.12.3, Hivemind 1.1.12, and PyTorch 2.10.0. It completed in 4.858 seconds. The selected full-range expert completed the first four-position forward, was stopped, and then produced two bounded pre-execution dial failures. Route attempt two used the other full-range peer, produced an exactly matching tensor, and completed without worker-side failed accounting. Both nodes, the client DHT, the cached remote-expert P2P client, and the bootstrap shut down successfully.
