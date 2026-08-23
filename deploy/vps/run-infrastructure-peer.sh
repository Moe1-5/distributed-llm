#!/usr/bin/env bash
set -euo pipefail

required=(
  DISTRIBLLM_INFRA_ROLE
  DISTRIBLLM_INFRA_HOST
  DISTRIBLLM_INFRA_PORT
  DISTRIBLLM_INFRA_ANNOUNCE_MADDR
  DISTRIBLLM_INFRA_IDENTITY_PATH
  DISTRIBLLM_INFRA_STATUS_PATH
  DISTRIBLLM_INFRA_FAILURE_DOMAIN
  DISTRIBLLM_DEPLOY_COMMIT
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required infrastructure environment variable: ${name}" >&2
    exit 64
  fi
done

role="$DISTRIBLLM_INFRA_ROLE"
if [[ "$role" != dht && "$role" != relay ]]; then
  echo "DISTRIBLLM_INFRA_ROLE must be dht or relay" >&2
  exit 64
fi
if [[ ! "$DISTRIBLLM_INFRA_PORT" =~ ^[0-9]+$ ]] ||
   (( DISTRIBLLM_INFRA_PORT < 1 || DISTRIBLLM_INFRA_PORT > 65535 )); then
  echo "DISTRIBLLM_INFRA_PORT must be between 1 and 65535" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_INFRA_ANNOUNCE_MADDR" != /*/tcp/"$DISTRIBLLM_INFRA_PORT" ]]; then
  echo "Infrastructure announce multiaddress must end with its configured TCP port" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_INFRA_IDENTITY_PATH" != "/var/lib/distribllm-$role/"* ]]; then
  echo "Infrastructure identity must stay in the role-specific state directory" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_INFRA_STATUS_PATH" != "/run/distribllm-$role/"* ]]; then
  echo "Infrastructure status must stay in the role-specific runtime directory" >&2
  exit 64
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Locked backend environment is missing; run the infrastructure installer first" >&2
  exit 69
fi

peer_args=()
while IFS= read -r peer; do
  [[ -n "$peer" ]] && peer_args+=(--initial-peer "$peer")
done < <(printf '%s' "${DISTRIBLLM_INFRA_INITIAL_PEERS:-}" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
if [[ "$role" == relay && ${#peer_args[@]} -eq 0 ]]; then
  echo "Relay role requires DISTRIBLLM_INFRA_INITIAL_PEERS" >&2
  exit 64
fi

exec .venv/bin/python bootstrap.py \
  --role "$role" \
  --host "$DISTRIBLLM_INFRA_HOST" \
  --port "$DISTRIBLLM_INFRA_PORT" \
  --identity_path "$DISTRIBLLM_INFRA_IDENTITY_PATH" \
  --announce-maddr "$DISTRIBLLM_INFRA_ANNOUNCE_MADDR" \
  --status-path "$DISTRIBLLM_INFRA_STATUS_PATH" \
  --deployment-commit "$DISTRIBLLM_DEPLOY_COMMIT" \
  --failure-domain "$DISTRIBLLM_INFRA_FAILURE_DOMAIN" \
  "${peer_args[@]}"
