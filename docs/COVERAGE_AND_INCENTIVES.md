# Coverage-Aware Serving and Useful-Work Incentives

## Layer ranges and routing

Layer ranges are half-open. A node serving `0-6` executes layers zero through five, and a node serving `6-12` completes the route.

The route planner chooses a complete subset of advertised ranges. It minimizes hop count, uses deterministic span ordering, and round-robins exact-span replicas. Extra overlaps remain visible as standby capacity and do not invalidate a complete route.

`GET /models/{model_id}/serving-plan?layer_count=N` returns current provider counts, missing ranges, a recommended range, current and projected routes, standby ranges, and a coverage revision. New clients include that revision in `POST /node/start`. The API returns HTTP 409 when the snapshot changed or when an already-covered range needs explicit redundancy confirmation.

## Useful-work proof

Incentives prove accepted inference service, not unrelated hash mining. Every installation has an Ed25519 application identity separate from its libp2p identity. Enabled serving nodes publish a signed binding between those identities and an optional receipt-capable expert UID.

For each selected RPC hop:

1. The generator signs a request containing the model revision, selected route, layer range, tensor commitment, session, nonce, and timestamp.
2. The worker verifies the request, performs the forward pass, commits to the output, and signs a service receipt.
3. The generator verifies the worker signature and output commitment, then signs acceptance.
4. The paired receipt is submitted asynchronously to settlement. Failed, unselected, malformed, replayed, or self-dealing work receives no credit.

Protocol version one supports the application's current generation batch size of one. Peers without receipt capability continue using the original inference expert.

## Settlement service

From `backend/`, start the VPS service with:

```bash
DISTRIBLLM_SETTLEMENT_MODE=shadow \
DISTRIBLLM_SETTLEMENT_HOST=127.0.0.1 \
DISTRIBLLM_SETTLEMENT_PORT=8010 \
python -m incentives.settlement
```

Put TLS and authentication-aware rate limiting at the VPS reverse proxy. Persist `DISTRIBLLM_SETTLEMENT_DATA_DIR` outside the application checkout and back up the SQLite database plus WAL files together.

The service exposes:

- `POST /v1/receipts`
- `GET /v1/accounts/{public_key}`
- `GET /v1/accounts/{public_key}/entries`
- `GET /v1/policy`

SQLite uses WAL mode, unique request and receipt hashes, immediate write transactions, and append-only receipt and ledger tables. The versioned integer reward is:

`position_count * served_layer_count * model_compute_weight * reward_scale`

## Rollout

Set clients and workers to `DISTRIBLLM_INCENTIVE_MODE=off`, `shadow`, or `credit`. Configure `DISTRIBLLM_SETTLEMENT_URL` on participating devices. Keep settlement in shadow mode through a real two-device relay run, inspect accepted and rejected receipts in the Incentives page, then switch the VPS and clients to credit mode only after the evidence is approved.

Credits are read-only accounting units. They do not transfer, withdraw, gate inference, or bypass Hugging Face authorization.
