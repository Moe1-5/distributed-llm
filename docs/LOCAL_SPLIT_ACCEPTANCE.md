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
