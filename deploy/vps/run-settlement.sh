#!/usr/bin/env bash
set -euo pipefail

required=(
  DISTRIBLLM_INCENTIVES_MODE
  DISTRIBLLM_SETTLEMENT_HOST
  DISTRIBLLM_SETTLEMENT_PORT
  DISTRIBLLM_SETTLEMENT_DB
  DISTRIBLLM_RECEIPT_TIMESTAMP_WINDOW
  DISTRIBLLM_DEPLOY_COMMIT
  DISTRIBLLM_FAILURE_DOMAIN
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required settlement environment variable: ${name}" >&2
    exit 64
  fi
done

if [[ "$DISTRIBLLM_INCENTIVES_MODE" != shadow && "$DISTRIBLLM_INCENTIVES_MODE" != credit ]]; then
  echo "VPS settlement mode must be shadow or credit" >&2
  exit 64
fi
if [[ ! "$DISTRIBLLM_SETTLEMENT_PORT" =~ ^[0-9]+$ ]] ||
   (( DISTRIBLLM_SETTLEMENT_PORT < 1 || DISTRIBLLM_SETTLEMENT_PORT > 65535 )); then
  echo "DISTRIBLLM_SETTLEMENT_PORT must be between 1 and 65535" >&2
  exit 64
fi
if [[ "$DISTRIBLLM_SETTLEMENT_DB" != /var/lib/distribllm-settlement/* ]]; then
  echo "Settlement database must stay under /var/lib/distribllm-settlement" >&2
  exit 64
fi
if [[ ! "$DISTRIBLLM_RECEIPT_TIMESTAMP_WINDOW" =~ ^[0-9]+$ ]] ||
   (( DISTRIBLLM_RECEIPT_TIMESTAMP_WINDOW < 30 )); then
  echo "Receipt timestamp window must be an integer of at least 30 seconds" >&2
  exit 64
fi
settlement_venv=/var/lib/distribllm-settlement/venv
if [[ ! -x "$settlement_venv/bin/uvicorn" ]]; then
  echo "Locked backend environment is missing; run the service installer first" >&2
  exit 69
fi

exec "$settlement_venv/bin/uvicorn" incentives.settlement:create_default_settlement_app \
  --factory \
  --host "$DISTRIBLLM_SETTLEMENT_HOST" \
  --port "$DISTRIBLLM_SETTLEMENT_PORT" \
  --no-access-log
