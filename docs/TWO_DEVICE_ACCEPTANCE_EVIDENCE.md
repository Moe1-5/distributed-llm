# Two-Device Acceptance Evidence

## Purpose

`backend/acceptance_evidence.py` captures a small, sanitized JSON record from each participant and validates that one real generation used a complete adjacent route owned by at least two participant labels. It covers the remaining relay, coverage, timing, and shadow-receipt evidence gates without relying on screenshots.

The collector includes public peer IDs, application public keys, selected and standby ranges, runtime versions, transport mode, timing, and accounting counters. It excludes Hugging Face tokens, local model paths, private keys, multiaddresses, prompts, and backend credentials. Output files are written with mode `0600`.

Participant labels are operator attestations. The validator proves that distinct local backend captures own the selected peer IDs, but it cannot cryptographically prove that those backends run on separate physical computers. Record the device names and observe both machines during final approval.

## Relay Acceptance

Prerequisites:

- Both devices run the same reviewed feature commit with Python 3.12 and the locked Hivemind environment.
- The persistent VPS relay and settlement services are healthy.
- Both participants use `DISTRIBLLM_NETWORK_MODE=auto`, the same bootstrap peer, the same trusted relay, and `DISTRIBLLM_INCENTIVES_MODE=shadow`.
- Device A serves OPT-125M layers `0-6` and device B serves layers `6-12`.
- Device B has a loaded generator for `facebook/opt-125m` after both ranges are visible.

After both nodes and the generator are ready, capture device A:

```bash
cd backend
uv run --python 3.12 python -m acceptance_evidence capture \
  --participant device-a \
  --backend-url http://127.0.0.1:8000 \
  --model facebook/opt-125m \
  --output ~/distribllm-evidence/device-a-relay.json
```

Run one deterministic inference while capturing device B:

```bash
cd backend
uv run --python 3.12 python -m acceptance_evidence capture \
  --participant device-b \
  --backend-url http://127.0.0.1:8000 \
  --model facebook/opt-125m \
  --run-inference \
  --max-new-tokens 8 \
  --settlement-wait 30 \
  --output ~/distribllm-evidence/device-b-relay.json
```

Transfer device A's JSON file to device B through the approved operator channel, then validate both:

```bash
cd backend
uv run --python 3.12 python -m acceptance_evidence validate \
  ~/distribllm-evidence/device-a-relay.json \
  ~/distribllm-evidence/device-b-relay.json \
  --model facebook/opt-125m \
  --expected-mode relay \
  --expected-incentives shadow \
  --output ~/distribllm-evidence/relay-report.json
```

A successful report has `ok: true`, two adjacent selected ranges, at least two distinct selected peer IDs and participant owners, verified relay transport, matching per-hop timing, generated tokens, zero pending submissions, and an increase in accepted shadow receipts during the generation.

## Direct Acceptance

Repeat the same workflow on a network where both serving peers are directly reachable. Save new files and change only the validation expectation:

```bash
uv run --python 3.12 python -m acceptance_evidence validate \
  ~/distribllm-evidence/device-a-direct.json \
  ~/distribllm-evidence/device-b-direct.json \
  --model facebook/opt-125m \
  --expected-mode direct \
  --expected-incentives shadow \
  --output ~/distribllm-evidence/direct-report.json
```

Do not reuse relay captures for the direct gate. Each selected provider must report `connection_mode: direct` and `transport_verified: true` in the new run.

## Standby Non-Payment

This check needs an additional backend peer advertising an exact replica or overlapping range that the serving plan classifies as standby. Capture that standby backend immediately before and after the generator capture:

```bash
uv run --python 3.12 python -m acceptance_evidence capture \
  --participant standby-before \
  --model facebook/opt-125m \
  --output ~/distribllm-evidence/standby-before.json

# Run the device B inference capture here.

uv run --python 3.12 python -m acceptance_evidence capture \
  --participant standby-after \
  --model facebook/opt-125m \
  --output ~/distribllm-evidence/standby-after.json
```

Add both snapshots to the normal relay validation:

```bash
uv run --python 3.12 python -m acceptance_evidence validate \
  ~/distribllm-evidence/device-a-relay.json \
  ~/distribllm-evidence/device-b-relay.json \
  --model facebook/opt-125m \
  --expected-mode relay \
  --expected-incentives shadow \
  --standby-before ~/distribllm-evidence/standby-before.json \
  --standby-after ~/distribllm-evidence/standby-after.json
```

The validator requires the peer to be classified as standby, absent from the executed route, and unchanged in both `requests_served` and `token_positions_served`. Do not run unrelated inference against that peer between snapshots.

## Remaining Manual Gates

The JSON report does not replace these operator observations:

- clean Windows installation and managed WSL first-run lifecycle;
- VPS service commit/version review, restart identity continuity, and post-restart external relay probe;
- confirmation that the captures came from separate physical devices;
- live TinyLlama quality and performance baseline;
- explicit review before changing incentives from shadow to credit.

Keep failed reports. Their `errors` array is the acceptance finding and should be added to the relevant sprint log without repeatedly rerunning the same failed system test.

## Final Cross-Sprint Manifest

After completing both relay and direct validation, collect these files in one approved operator directory:

- two packaged Windows lifecycle reports exported from Settings after clean backend stops;
- the live VPS restart report from `validate-bootstrap-service.sh --restart-test`;
- the post-restart relay probe bound to that VPS report with `--validation-context`;
- the passing relay inference report;
- the passing direct inference report.

Validate that they form one compatible set:

```bash
cd backend
uv run --python 3.12 python -m acceptance_manifest \
  --windows-report ~/distribllm-evidence/device-a-windows.json \
  --windows-report ~/distribllm-evidence/device-b-windows.json \
  --vps-report ~/distribllm-evidence/vps-restart.json \
  --relay-probe ~/distribllm-evidence/post-restart-relay-probe.json \
  --relay-report ~/distribllm-evidence/relay-report.json \
  --direct-report ~/distribllm-evidence/direct-report.json \
  --model facebook/opt-125m \
  --expected-app-version 1.0.0 \
  --output ~/distribllm-evidence/final-acceptance.json
```

The validator requires two passing packaged-Windows and WSL lifecycle reports on the same application version, clean source commit, and executable SHA-256; a passing VPS identity-preserving restart; effective relay flags; a timely Hivemind 1.1.12 circuit reservation through that exact VPS report; passing relay and direct split inference for the same participant labels; and shadow-mode incentives. It writes the final report with mode `0600`.

`ok: true` means the artifacts are internally compatible and ready for review. `final_approval` deliberately remains `pending_manual_review`: software cannot prove that operator labels correspond to separate physical devices or that the visible output and Monitoring UI were reviewed. Do not close a sprint or enable credit mode from the automated flag alone.
