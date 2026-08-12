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

Local tests prove signatures, canonicalization, BLAKE3 commitments, countersignatures, replay and self-dealing rejection, policy bounds, concurrent writes, pagination, restart durability, rollout modes, and legacy inference compatibility. A real independent client-mode Hivemind peer also forwards a variable-length tensor through the receipt expert and settles the accepted signed response. Two separate devices must still prove receipt-capable inference through the VPS relay, shadow submission, settlement restart continuity, and zero credit for standby or failed work before credit mode is approved.
