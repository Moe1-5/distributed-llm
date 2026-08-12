#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
  echo "Run this installer with sudo." >&2
  exit 77
fi

repo_root="${1:-/opt/distribllm}"
repo_root="$(readlink -f "$repo_root")"
if [[ ! -f "$repo_root/backend/pyproject.toml" ]] || [[ ! -d "$repo_root/.git" ]]; then
  echo "Repository checkout is invalid: $repo_root" >&2
  exit 66
fi

uv_bin="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$uv_bin" ]]; then
  echo "uv is required before installing the service." >&2
  exit 69
fi

id -u distribllm >/dev/null 2>&1 || \
  useradd --system --home-dir /var/lib/distribllm --shell /usr/sbin/nologin distribllm
install -d -m 0750 -o distribllm -g distribllm /var/lib/distribllm
install -d -m 0750 -o root -g distribllm /etc/distribllm
if [[ ! -f /etc/distribllm/settlement.env ]]; then
  install -m 0640 -o root -g distribllm \
    "$repo_root/deploy/vps/settlement.env.example" /etc/distribllm/settlement.env
fi

"$uv_bin" sync --frozen --python 3.12 --project "$repo_root/backend"
chmod 0755 "$repo_root/deploy/vps/run-settlement.sh"

unit_tmp="$(mktemp)"
sed "s|@REPO_ROOT@|$repo_root|g" \
  "$repo_root/deploy/vps/distribllm-settlement.service" > "$unit_tmp"
install -m 0644 "$unit_tmp" /etc/systemd/system/distribllm-settlement.service
rm -f "$unit_tmp"

systemctl daemon-reload
systemctl enable distribllm-settlement.service
systemctl restart distribllm-settlement.service
echo "Settlement service installed in shadow mode by default."
