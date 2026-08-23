#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
  echo "Run this validator with sudo." >&2
  exit 77
fi
role="${1:-}"
if [[ "$role" != dht && "$role" != relay ]]; then
  echo "Usage: $0 <dht|relay> [--restart-test]" >&2
  exit 64
fi
restart_test=false
if [[ "${2:-}" == "--restart-test" ]]; then
  restart_test=true
elif [[ $# -gt 1 ]]; then
  echo "Usage: $0 <dht|relay> [--restart-test]" >&2
  exit 64
fi

service_name="distribllm-$role.service"
environment_file="/etc/distribllm-$role/service.env"
source "$environment_file"
[[ "$DISTRIBLLM_INFRA_ROLE" == "$role" ]] || {
  echo "Service role does not match its environment file." >&2
  exit 64
}
systemctl is-enabled --quiet "$service_name"
systemctl is-active --quiet "$service_name"

repo_root="$(systemctl show -p WorkingDirectory --value "$service_name")"
status_path="$DISTRIBLLM_INFRA_STATUS_PATH"
for _ in {1..30}; do
  [[ -s "$status_path" ]] && break
  sleep 1
done
[[ -s "$status_path" ]] || {
  echo "Runtime status file was not created: $status_path" >&2
  exit 1
}

before_status=""
before_identity_hash=""
cleanup() {
  [[ -n "$before_status" ]] && rm -f "$before_status"
}
trap cleanup EXIT
if $restart_test; then
  before_status="$(mktemp)"
  cp "$status_path" "$before_status"
  before_identity_hash="$(sha256sum "$DISTRIBLLM_INFRA_IDENTITY_PATH" | cut -d' ' -f1)"
  systemctl restart "$service_name"
  for _ in {1..30}; do
    if [[ -s "$status_path" ]] && ! cmp -s "$status_path" "$before_status"; then break; fi
    sleep 1
  done
  systemctl is-active --quiet "$service_name"
fi

public_maddr="${DISTRIBLLM_INFRA_ANNOUNCE_MADDR}/p2p/${DISTRIBLLM_EXPECTED_PEER_ID}"
validator_args=(
  --status "$status_path"
  --expected-role "$role"
  --expected-peer-id "$DISTRIBLLM_EXPECTED_PEER_ID"
  --expected-hivemind-version "${DISTRIBLLM_EXPECTED_HIVEMIND_VERSION:-1.1.12}"
  --expected-commit "$DISTRIBLLM_DEPLOY_COMMIT"
  --expected-identity-path "$DISTRIBLLM_INFRA_IDENTITY_PATH"
  --expected-public-maddr "$public_maddr"
  --expected-port "$DISTRIBLLM_INFRA_PORT"
  --expected-failure-domain "$DISTRIBLLM_INFRA_FAILURE_DOMAIN"
)
logs="$(journalctl -u "$service_name" -n 500 --no-pager)"
expected_flags=('-forceReachabilityPublic=1')
if [[ "$role" == dht ]]; then
  expected_flags+=('-dhtServer=1' '-relay=0')
else
  expected_flags+=('-dhtClient=1' '-relay=1')
fi
for flag in "${expected_flags[@]}"; do
  grep -F -- "$flag" <<< "$logs" >/dev/null || {
    echo "Expected effective p2pd flag not found in recent service logs: $flag" >&2
    exit 1
  }
done
validator_args+=(--effective-flags-observed)

if $restart_test; then
  after_identity_hash="$(sha256sum "$DISTRIBLLM_INFRA_IDENTITY_PATH" | cut -d' ' -f1)"
  [[ "$before_identity_hash" == "$after_identity_hash" ]] || {
    echo "Persistent identity file changed across restart." >&2
    exit 1
  }
  validator_args+=(--before-restart-status "$before_status" --identity-hash-preserved)
fi

"$repo_root/.venv/bin/python" "$repo_root/bootstrap_service_validate.py" "${validator_args[@]}"
