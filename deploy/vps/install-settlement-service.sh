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
if [[ -z "$uv_bin" ]]; then
  echo "uv is required before installing the service." >&2
  exit 69
fi
if [[ ! -x "$uv_bin" ]]; then
  echo "uv is not executable: $uv_bin" >&2
  exit 69
fi

service_user=distribllm-settlement
state_dir=/var/lib/distribllm-settlement
config_dir=/etc/distribllm-settlement
environment_file="$config_dir/service.env"
id -u "$service_user" >/dev/null 2>&1 || \
  useradd --system --home-dir "$state_dir" --shell /usr/sbin/nologin "$service_user"
install -d -m 0750 -o "$service_user" -g "$service_user" "$state_dir"
install -d -m 0750 -o "$service_user" -g "$service_user" "$state_dir/.cache"
install -d -m 0750 -o "$service_user" -g "$service_user" "$state_dir/uv-cache"
install -d -m 0750 -o "$service_user" -g "$service_user" "$state_dir/uv-python"
install -d -m 0750 -o root -g "$service_user" "$config_dir"
install -d -m 0755 -o root -g root /usr/local/libexec/distribllm
install -m 0755 -o root -g root "$uv_bin" /usr/local/libexec/distribllm/uv
if [[ ! -f "$environment_file" ]]; then
  install -m 0640 -o root -g "$service_user" \
    "$repo_root/deploy/vps/settlement.env.example" "$environment_file"
fi

commit="$(git -C "$repo_root" rev-parse HEAD)"
environment_tmp="$(mktemp)"
awk -v commit="$commit" '
  BEGIN { updated = 0 }
  /^DISTRIBLLM_DEPLOY_COMMIT=/ { print "DISTRIBLLM_DEPLOY_COMMIT=" commit; updated = 1; next }
  { print }
  END { if (!updated) print "DISTRIBLLM_DEPLOY_COMMIT=" commit }
' "$environment_file" > "$environment_tmp"
install -m 0640 -o root -g "$service_user" "$environment_tmp" "$environment_file"
rm -f "$environment_tmp"

runuser -u "$service_user" -- env \
  HOME="$state_dir" \
  XDG_CACHE_HOME="$state_dir/.cache" \
  UV_CACHE_DIR="$state_dir/uv-cache" \
  UV_PYTHON_INSTALL_DIR="$state_dir/uv-python" \
  UV_PROJECT_ENVIRONMENT="$state_dir/venv" \
  /usr/local/libexec/distribllm/uv \
  sync --frozen --python 3.12 --project "$repo_root/backend"
chmod 0755 "$repo_root/deploy/vps/run-settlement.sh"
chmod 0755 "$repo_root/deploy/vps/validate-control-service.sh"

unit_tmp="$(mktemp)"
sed "s|@REPO_ROOT@|$repo_root|g" \
  "$repo_root/deploy/vps/distribllm-settlement.service" > "$unit_tmp"
install -m 0644 "$unit_tmp" /etc/systemd/system/distribllm-settlement.service
rm -f "$unit_tmp"

systemctl daemon-reload
if grep -q 'REPLACE_' "$environment_file"; then
  echo "Settlement unit installed but not started; replace its failure-domain placeholder first." >&2
  exit 0
fi
systemctl enable distribllm-settlement.service
systemctl restart distribllm-settlement.service
echo "Settlement service installed at commit $commit in shadow mode by default."
