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
if [[ "$repo_root" == *$'\n'* ]] || [[ "$repo_root" == *'@'* ]]; then
  echo "Repository path contains unsupported characters." >&2
  exit 64
fi

uv_bin="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$uv_bin" ]] || [[ ! -x "$uv_bin" ]]; then
  echo "An executable uv binary is required before installing the service." >&2
  exit 69
fi

id -u distribllm >/dev/null 2>&1 || \
  useradd --system --home-dir /var/lib/distribllm --shell /usr/sbin/nologin distribllm
install -d -m 0750 -o distribllm -g distribllm /var/lib/distribllm
install -d -m 0750 -o distribllm -g distribllm /var/lib/distribllm/.cache
install -d -m 0750 -o distribllm -g distribllm /var/lib/distribllm/uv-cache
install -d -m 0750 -o distribllm -g distribllm /var/lib/distribllm/uv-python
install -d -m 0750 -o root -g distribllm /etc/distribllm
install -d -m 0755 -o root -g root /usr/local/libexec/distribllm
install -m 0755 -o root -g root "$uv_bin" /usr/local/libexec/distribllm/uv
if [[ ! -f /etc/distribllm/placement.env ]]; then
  install -m 0640 -o root -g distribllm \
    "$repo_root/deploy/vps/placement.env.example" /etc/distribllm/placement.env
fi

runuser -u distribllm -- env \
  HOME=/var/lib/distribllm \
  XDG_CACHE_HOME=/var/lib/distribllm/.cache \
  UV_CACHE_DIR=/var/lib/distribllm/uv-cache \
  UV_PYTHON_INSTALL_DIR=/var/lib/distribllm/uv-python \
  UV_PROJECT_ENVIRONMENT=/var/lib/distribllm/placement-venv \
  /usr/local/libexec/distribllm/uv \
  sync --frozen --python 3.12 --project "$repo_root/backend"
chmod 0755 "$repo_root/deploy/vps/run-placement.sh"

unit_tmp="$(mktemp)"
sed "s|@REPO_ROOT@|$repo_root|g" \
  "$repo_root/deploy/vps/distribllm-placement.service" > "$unit_tmp"
install -m 0644 "$unit_tmp" /etc/systemd/system/distribllm-placement.service
rm -f "$unit_tmp"

systemctl daemon-reload
systemctl enable distribllm-placement.service
systemctl restart distribllm-placement.service
echo "Placement service installed. Set real independent secrets before participant use."
