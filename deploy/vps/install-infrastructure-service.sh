#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
  echo "Run this installer with sudo." >&2
  exit 77
fi
role="${1:-}"
repo_root="${2:-/opt/distribllm}"
if [[ "$role" != dht && "$role" != relay ]]; then
  echo "Usage: $0 <dht|relay> [repository-root]" >&2
  exit 64
fi
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
if [[ -z "$uv_bin" || ! -x "$uv_bin" ]]; then
  echo "An executable uv binary is required before installing the service." >&2
  exit 69
fi

service_user="distribllm-$role"
state_dir="/var/lib/$service_user"
config_dir="/etc/$service_user"
environment_file="$config_dir/service.env"
service_name="distribllm-$role.service"

id -u "$service_user" >/dev/null 2>&1 || \
  useradd --system --home-dir "$state_dir" --shell /usr/sbin/nologin "$service_user"
install -d -m 0750 -o "$service_user" -g "$service_user" "$state_dir"
install -d -m 0750 -o root -g "$service_user" "$config_dir"
if [[ ! -f "$environment_file" ]]; then
  install -m 0640 -o root -g "$service_user" \
    "$repo_root/deploy/vps/$role.env.example" "$environment_file"
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

"$uv_bin" sync --frozen --python 3.12 --project "$repo_root/backend"
chmod 0755 "$repo_root/deploy/vps/run-infrastructure-peer.sh"
chmod 0755 "$repo_root/deploy/vps/validate-infrastructure-service.sh"

unit_tmp="$(mktemp)"
sed "s|@REPO_ROOT@|$repo_root|g" \
  "$repo_root/deploy/vps/$service_name" > "$unit_tmp"
install -m 0644 "$unit_tmp" "/etc/systemd/system/$service_name"
rm -f "$unit_tmp"
systemctl daemon-reload

if grep -q 'REPLACE_' "$environment_file"; then
  echo "Installed $service_name but did not start it." >&2
  echo "Replace every placeholder in $environment_file, then rerun this installer." >&2
  exit 0
fi

systemctl enable "$service_name"
systemctl restart "$service_name"
echo "Installed $service_name at commit $commit." >&2
echo "Validate with: sudo $repo_root/deploy/vps/validate-infrastructure-service.sh $role --restart-test" >&2
