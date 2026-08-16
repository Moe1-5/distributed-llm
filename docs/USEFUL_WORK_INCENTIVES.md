# Useful-Work Incentives

## Scope

Sprint 13 accounts for accepted distributed inference work. It does not mine unrelated hashes, transfer credits, withdraw value, bypass model authorization, or charge for ordinary inference. Credits are integer accounting units and credit mode remains an explicit rollout gate.

## Protocol

Each process owns a persistent Ed25519 application identity independent of its Hivemind peer identity. A short-lived signed presence record binds that application public key to the current p2p peer ID. Private key material stays in the local identity file with mode `0600` and is never returned by the API.

The existing inference expert remains unchanged. In shadow or credit mode, a worker advertises a second receipt-protocol version one expert. The generator sends canonical JSON through a fixed-size byte tensor containing:

- request and session IDs;
- the complete adjacent selected route and its BLAKE3 route ID;
- model name and allowed revision;
- selected worker identity, RPC UID, and half-open layer range;
- position count and input tensor commitment;
- nonce and timestamp.

The worker verifies the signature, tensor commitment, selected-route membership, model, revision, and served range before inference. It signs the response commitment and counters. The generator verifies the returned tensor, shape, finite values, signature, commitments, and paired fields before countersigning acceptance. Receipts remain local until every hop in that forward pass succeeds. A receipt-path failure falls back to the legacy expert without credit; a later route failure discards all pending receipts from that pass.

## Settlement

The FastAPI service stores SQLite in WAL mode with append-only policy versions, identities, signed presence bindings, receipt pairs, and ledger entries. It rejects duplicate request IDs, receipt hashes, worker nonces, invalid signatures, stale timestamps, self-dealing identities, altered counters, incomplete routes, workers outside the route, unsupported revisions, and out-of-bounds ranges.

Reward policy version one uses:

`position_count * served_layer_count * model_compute_weight * reward_scale`

Hardware claims and latency do not affect rewards. Shadow mode validates and stores receipts without ledger entries. Credit mode adds a ledger entry. Off mode disables receipt work entirely.

Public endpoints are:

- `POST /v1/receipts`
- `GET /v1/accounts/{public_key}`
- `GET /v1/accounts/{public_key}/entries`
- `GET /v1/policy`

## Developer API Access

Electron chat remains free and uses the local `/chat` and `/stream` endpoints without an API key. Developer integrations use the separate OpenAI-compatible `POST /v1/chat/completions` endpoint. A positive verified useful-work balance is required to create a key. Keys use high-entropy `dllm_` tokens, are returned once, are stored only as BLAKE3 hashes, and can be revoked from the Incentives view.

Access policy is independent from receipt rollout:

- `off` disables the developer endpoint while preserving Electron chat.
- `shadow` requires a valid key and records projected model-weighted usage without spending credits.
- `enforced` atomically reserves the maximum request cost, charges completed positions, and releases failed, cancelled, or unused reservations.

Pricing version one uses shared credits across every local key owned by the same application identity:

`position_count * model_compute_weight * api_price_scale`

The local API signs a short-lived Ed25519 inference capability for each accepted request and consumes its nonce before generation. Forged, expired, replayed, wrong-model, or oversized capabilities fail before execution. The bearer key terminates at the local API and is never sent to a serving worker.

For example, after creating a key in the Incentives view and starting a ready generator:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer dllm_REPLACE_WITH_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"facebook/opt-125m","messages":[{"role":"user","content":"Hello"}],"max_tokens":16}'
```

The managed backend now binds to `127.0.0.1` by default so local key-management endpoints are not exposed to the LAN accidentally. Key-management requests also reject non-local browser origins. An explicit non-loopback bind requires a separately reviewed authentication and TLS deployment.

The local spend ledger is phase one. A later hosted project gateway must perform globally atomic reservations across devices using the same API contract; local enforced mode must not be treated as a global multi-device payment system.

## VPS Rollout

1. Install with `sudo deploy/vps/install-settlement-service.sh /opt/distribllm`.
2. Keep `/etc/distribllm/settlement.env` in `shadow` mode during protocol review.
3. Expose the local listener through the VPS HTTPS reverse proxy and set clients' `DISTRIBLLM_SETTLEMENT_URL` to that origin.
4. Set clients and workers to `shadow`, run two-device inference, and inspect rejected receipts and restart durability.
5. Back up the SQLite database and its WAL files while using SQLite's online backup mechanism or after stopping the service.
6. Change the VPS and participating clients to `credit` only after shadow evidence is approved, then restart the service and clients.

The service records an explicit database schema version and refuses unknown or incompatible pre-release schemas. Back up an incompatible database and point `DISTRIBLLM_SETTLEMENT_DB` at a new path until an approved migration exists; never delete ledger state as an automatic recovery step. Database, WAL, and shared-memory files are restricted to the service account.

Check service health with `systemctl status distribllm-settlement`, policy with `curl http://127.0.0.1:7101/v1/policy`, and logs with `journalctl -u distribllm-settlement -n 200 --no-pager`.

## Remaining Acceptance

Local tests prove signatures, canonicalization, BLAKE3 commitments, countersignatures, replay and self-dealing rejection, policy bounds, concurrent writes, pagination, restart durability, rollout modes, and legacy inference compatibility. A real independent client-mode Hivemind peer also forwards a variable-length tensor through the receipt expert and settles the accepted signed response.

The [two-device evidence runbook](TWO_DEVICE_ACCEPTANCE_EVIDENCE.md) now captures selected-route ownership, per-hop transport and timing, an accepted-receipt increase, settlement drain, and optional standby before/after counters. Two separate devices must still execute that run through the live VPS relay, prove settlement restart continuity, and receive explicit review before credit mode is approved.
