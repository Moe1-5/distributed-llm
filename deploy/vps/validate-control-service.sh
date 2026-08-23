#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
  echo "Run this validator with sudo." >&2
  exit 77
fi
role="${1:-}"
if [[ "$role" != coordinator && "$role" != settlement ]]; then
  echo "Usage: $0 <coordinator|settlement> [--restart-test]" >&2
  exit 64
fi
restart_test=false
if [[ "${2:-}" == "--restart-test" ]]; then
  restart_test=true
elif [[ $# -gt 1 ]]; then
  echo "Usage: $0 <coordinator|settlement> [--restart-test]" >&2
  exit 64
fi

if [[ "$role" == coordinator ]]; then
  service_name=distribllm-placement.service
  environment_file=/etc/distribllm-placement/service.env
  source "$environment_file"
  host="$DISTRIBLLM_PLACEMENT_HOST"
  port="$DISTRIBLLM_PLACEMENT_PORT"
else
  service_name=distribllm-settlement.service
  environment_file=/etc/distribllm-settlement/service.env
  source "$environment_file"
  host="$DISTRIBLLM_SETTLEMENT_HOST"
  port="$DISTRIBLLM_SETTLEMENT_PORT"
fi
[[ "$host" == 127.0.0.1 || "$host" == localhost ]] || {
  echo "Control-service validator only probes the locked loopback deployment." >&2
  exit 64
}

systemctl is-enabled --quiet "$service_name"
systemctl is-active --quiet "$service_name"
repo_root="$(systemctl show -p WorkingDirectory --value "$service_name")"
before_pid="$(systemctl show -p MainPID --value "$service_name")"
restart_args=()
if $restart_test; then
  systemctl restart "$service_name"
  restart_args+=(--restart-requested)
fi

health_file="$(mktemp)"
cleanup() { rm -f "$health_file"; }
trap cleanup EXIT
for _ in {1..30}; do
  if curl --fail --silent --show-error "http://$host:$port/health" -o "$health_file"; then
    break
  fi
  sleep 1
done
[[ -s "$health_file" ]] || { echo "Control service health did not become ready." >&2; exit 1; }
after_pid="$(systemctl show -p MainPID --value "$service_name")"
if $restart_test && [[ "$before_pid" != "$after_pid" && "$after_pid" != 0 ]]; then
  restart_args+=(--restart-pid-changed)
fi
journalctl -u "$service_name" -n 1 --no-pager --quiet | grep -q . || {
  echo "No service journal entry was observed." >&2
  exit 1
}

"$repo_root/.venv/bin/python" "$repo_root/control_service_validate.py" \
  --health "$health_file" \
  --role "$role" \
  --expected-commit "$DISTRIBLLM_DEPLOY_COMMIT" \
  --expected-failure-domain "$DISTRIBLLM_FAILURE_DOMAIN" \
  --journal-observed \
  "${restart_args[@]}"
