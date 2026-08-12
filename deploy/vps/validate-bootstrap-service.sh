#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
  echo "Run this validator with sudo so it can inspect service state and restart safely." >&2
  exit 77
fi

restart_test=false
if [[ "${1:-}" == "--restart-test" ]]; then
  restart_test=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--restart-test]" >&2
  exit 64
fi

service_name=distribllm-bootstrap.service
environment_file=/etc/distribllm/bootstrap.env
source "$environment_file"

systemctl is-enabled --quiet "$service_name"
systemctl is-active --quiet "$service_name"

repo_root="$(systemctl show -p WorkingDirectory --value "$service_name")"
status_path="${DISTRIBLLM_BOOTSTRAP_STATUS_PATH:-/run/distribllm/bootstrap-status.json}"
for _ in {1..30}; do
  [[ -s "$status_path" ]] && break
  sleep 1
done
[[ -s "$status_path" ]] || { echo "Runtime status file was not created: $status_path" >&2; exit 1; }

before_status=""
before_identity_hash=""
cleanup() {
  [[ -n "$before_status" ]] && rm -f "$before_status"
}
trap cleanup EXIT
if $restart_test; then
  before_status="$(mktemp)"
  cp "$status_path" "$before_status"
  before_identity_hash="$(sha256sum "$DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH" | cut -d' ' -f1)"
  systemctl restart "$service_name"
  for _ in {1..30}; do
    if [[ -s "$status_path" ]] && ! cmp -s "$status_path" "$before_status"; then break; fi
    sleep 1
  done
  systemctl is-active --quiet "$service_name"
fi

public_maddr="${DISTRIBLLM_BOOTSTRAP_ANNOUNCE_MADDR}/p2p/${DISTRIBLLM_EXPECTED_PEER_ID}"
validator_args=(
  --status "$status_path"
  --expected-peer-id "$DISTRIBLLM_EXPECTED_PEER_ID"
  --expected-hivemind-version "${DISTRIBLLM_EXPECTED_HIVEMIND_VERSION:-1.1.12}"
  --expected-commit "$DISTRIBLLM_DEPLOY_COMMIT"
  --expected-identity-path "$DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH"
  --expected-public-maddr "$public_maddr"
  --expected-port "$DISTRIBLLM_BOOTSTRAP_PORT"
)
logs="$(journalctl -u "$service_name" -n 500 --no-pager)"
for flag in '-relay=1' '-forceReachabilityPublic=1'; do
  grep -F -- "$flag" <<< "$logs" >/dev/null || {
    echo "Expected effective p2pd flag not found in recent service logs: $flag" >&2
    exit 1
  }
done
validator_args+=(--relay-flags-observed)

if $restart_test; then
  after_identity_hash="$(sha256sum "$DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH" | cut -d' ' -f1)"
  [[ "$before_identity_hash" == "$after_identity_hash" ]] || {
    echo "Persistent identity file changed across restart." >&2
    exit 1
  }
  validator_args+=(
    --before-restart-status "$before_status"
    --identity-hash-preserved
  )
fi

"$repo_root/.venv/bin/python" "$repo_root/bootstrap_service_validate.py" "${validator_args[@]}"
echo "Service, runtime evidence, relay flags, and identity continuity are valid." >&2
