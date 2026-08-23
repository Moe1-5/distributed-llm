#!/usr/bin/env bash
set -euo pipefail

required=(
  DISTRIBLLM_PLACEMENT_HOST
  DISTRIBLLM_PLACEMENT_PORT
  DISTRIBLLM_PLACEMENT_DB
  DISTRIBLLM_PLACEMENT_AUTH_TOKEN
  DISTRIBLLM_PLACEMENT_TOKEN_SECRET
  DISTRIBLLM_PLACEMENT_STARTUP_TTL_SECONDS
  DISTRIBLLM_PLACEMENT_ONLINE_TTL_SECONDS
  DISTRIBLLM_DEPLOY_COMMIT
  DISTRIBLLM_FAILURE_DOMAIN
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required placement environment variable: ${name}" >&2
    exit 64
  fi
done

if [[ ! "$DISTRIBLLM_PLACEMENT_PORT" =~ ^[0-9]+$ ]] ||
   (( DISTRIBLLM_PLACEMENT_PORT < 1 || DISTRIBLLM_PLACEMENT_PORT > 65535 )); then
  echo "DISTRIBLLM_PLACEMENT_PORT must be between 1 and 65535" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_PLACEMENT_DB" != /var/lib/distribllm-placement/* ]]; then
  echo "Placement database must stay under /var/lib/distribllm-placement" >&2
  exit 64
fi
if (( ${#DISTRIBLLM_PLACEMENT_AUTH_TOKEN} < 32 )); then
  echo "Placement auth token must contain at least 32 characters" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_PLACEMENT_AUTH_TOKEN" == REPLACE_* ]] ||
   [[ "$DISTRIBLLM_PLACEMENT_TOKEN_SECRET" == REPLACE_* ]]; then
  echo "Replace the placement secret placeholders before starting the service" >&2
  exit 64
fi
if (( ${#DISTRIBLLM_PLACEMENT_TOKEN_SECRET} < 32 )); then
  echo "Placement token secret must contain at least 32 characters" >&2
  exit 64
fi

placement_venv=/var/lib/distribllm-placement/venv
if [[ ! -x "$placement_venv/bin/uvicorn" ]]; then
  echo "Locked backend environment is missing; run the placement installer first" >&2
  exit 69
fi

exec "$placement_venv/bin/uvicorn" placement.service:create_default_placement_app \
  --factory \
  --host "$DISTRIBLLM_PLACEMENT_HOST" \
  --port "$DISTRIBLLM_PLACEMENT_PORT" \
  --no-access-log
