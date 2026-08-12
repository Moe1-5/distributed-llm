#!/usr/bin/env bash
set -euo pipefail

required=(
  DISTRIBLLM_BOOTSTRAP_HOST
  DISTRIBLLM_BOOTSTRAP_PORT
  DISTRIBLLM_BOOTSTRAP_ANNOUNCE_MADDR
  DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH
  DISTRIBLLM_BOOTSTRAP_STATUS_PATH
  DISTRIBLLM_DEPLOY_COMMIT
)

for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required service environment variable: ${name}" >&2
    exit 64
  fi
done

if [[ ! "$DISTRIBLLM_BOOTSTRAP_PORT" =~ ^[0-9]+$ ]] ||
   (( DISTRIBLLM_BOOTSTRAP_PORT < 1 || DISTRIBLLM_BOOTSTRAP_PORT > 65535 )); then
  echo "DISTRIBLLM_BOOTSTRAP_PORT must be between 1 and 65535" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_BOOTSTRAP_ANNOUNCE_MADDR" != /*/tcp/"$DISTRIBLLM_BOOTSTRAP_PORT" ]]; then
  echo "Announce multiaddress must end with the configured TCP port" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH" != /var/lib/distribllm/* ]]; then
  echo "Bootstrap identity must stay under /var/lib/distribllm" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_BOOTSTRAP_STATUS_PATH" != /run/distribllm/* ]]; then
  echo "Bootstrap status must stay under /run/distribllm" >&2
  exit 64
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Locked backend environment is missing; run the service installer first" >&2
  exit 69
fi

exec .venv/bin/python bootstrap.py \
  --host "$DISTRIBLLM_BOOTSTRAP_HOST" \
  --port "$DISTRIBLLM_BOOTSTRAP_PORT" \
  --identity_path "$DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH" \
  --announce-maddr "$DISTRIBLLM_BOOTSTRAP_ANNOUNCE_MADDR" \
  --status-path "$DISTRIBLLM_BOOTSTRAP_STATUS_PATH" \
  --deployment-commit "$DISTRIBLLM_DEPLOY_COMMIT"
