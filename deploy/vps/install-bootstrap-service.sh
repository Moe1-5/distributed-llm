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

commit="$(git -C "$repo_root" rev-parse HEAD)"
id -u distribllm >/dev/null 2>&1 || \
  useradd --system --home-dir /var/lib/distribllm --shell /usr/sbin/nologin distribllm

install -d -m 0750 -o distribllm -g distribllm /var/lib/distribllm
install -d -m 0750 -o root -g distribllm /etc/distribllm

if [[ ! -f /etc/distribllm/bootstrap.env ]]; then
  install -m 0640 -o root -g distribllm \
    "$repo_root/deploy/vps/bootstrap.env.example" /etc/distribllm/bootstrap.env
fi

environment_tmp="$(mktemp)"
awk -v commit="$commit" '
  BEGIN { updated = 0 }
  /^DISTRIBLLM_DEPLOY_COMMIT=/ { print "DISTRIBLLM_DEPLOY_COMMIT=" commit; updated = 1; next }
  { print }
  END { if (!updated) print "DISTRIBLLM_DEPLOY_COMMIT=" commit }
' /etc/distribllm/bootstrap.env > "$environment_tmp"
install -m 0640 -o root -g distribllm "$environment_tmp" /etc/distribllm/bootstrap.env
rm -f "$environment_tmp"

identity_path="$(awk -F= '
  $1 == "DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH" {
    sub(/^[^=]*=/, "")
    print
    exit
  }
' /etc/distribllm/bootstrap.env)"
status_path="$(awk -F= '
  $1 == "DISTRIBLLM_BOOTSTRAP_STATUS_PATH" {
    sub(/^[^=]*=/, "")
    print
    exit
  }
' /etc/distribllm/bootstrap.env)"
if [[ "$identity_path" != /var/lib/distribllm/* ]]; then
  echo "Bootstrap identity must stay under /var/lib/distribllm." >&2
  exit 64
fi
if [[ "$status_path" != /run/distribllm/* ]]; then
  echo "Bootstrap status must stay under /run/distribllm." >&2
  exit 64
fi
if [[ -e "$identity_path" ]]; then
  [[ -f "$identity_path" && ! -L "$identity_path" ]] || {
    echo "Bootstrap identity must be a regular file, not a symlink." >&2
    exit 65
  }
  chown distribllm:distribllm "$identity_path"
  chmod 0600 "$identity_path"
fi

"$uv_bin" sync --frozen --python 3.12 --project "$repo_root/backend"
chmod 0755 "$repo_root/deploy/vps/run-bootstrap.sh"

unit_tmp="$(mktemp)"
sed "s|@REPO_ROOT@|$repo_root|g" \
  "$repo_root/deploy/vps/distribllm-bootstrap.service" > "$unit_tmp"
install -m 0644 "$unit_tmp" /etc/systemd/system/distribllm-bootstrap.service
rm -f "$unit_tmp"

systemctl daemon-reload
systemctl enable distribllm-bootstrap.service
systemctl restart distribllm-bootstrap.service
echo "Installed commit $commit from $repo_root"
echo "Validate with: sudo $repo_root/deploy/vps/validate-bootstrap-service.sh"
