#!/usr/bin/env bash
set -Eeuo pipefail

work="${1:?Source directory required}"
digest="${2:?Source checksum required}"
settings="${3:?Encoded settings required}"
request_id="${4:?Request ID required}"
stage_name="initialization"
stage_started=$SECONDS
timings=""
exec > >(tee -a /var/log/orders-deployment.log) 2>&1

failure() {
  local code=$?
  echo "[vm:${stage_name}] FAILED (exit ${code}). See /var/log/orders-deployment.log."
  journalctl --unit orders-api --no-pager --lines 15 || true
  exit "${code}"
}
trap failure ERR

start_stage() {
  if [[ "${stage_name}" != "initialization" ]]; then
    timings="${timings}\"${stage_name}\":$((SECONDS - stage_started)),"
    echo "[vm:${stage_name}] Completed in $((SECONDS - stage_started))s"
  fi
  stage_name="$1"
  stage_started=$SECONDS
  echo "[vm:${stage_name}] Starting"
}

[[ $EUID -eq 0 ]] || { echo "Deployment must run as root through Azure Run Command."; exit 1; }
[[ "${digest}" =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid source checksum."; exit 1; }

start_stage cloud-init
timeout 180 cloud-init status --wait

start_stage data-disk
device_link=/dev/disk/azure/scsi1/lun0
for _ in {1..30}; do
  [[ -b "${device_link}" ]] && break
  sleep 2
done
[[ -b "${device_link}" ]] || { echo "Managed data disk LUN 0 did not appear."; exit 1; }
device=$(readlink --canonicalize "${device_link}")
if file_system=$(blkid -s TYPE -o value "${device}"); then
  [[ "${file_system}" == "ext4" ]] || { echo "Refusing to overwrite a non-ext4 data disk."; exit 1; }
  [[ "$(blkid -s LABEL -o value "${device}")" == "orders-data" ]] \
    || { echo "Refusing to adopt an unrelated ext4 data disk."; exit 1; }
else
  code=$?
  [[ "${code}" -eq 2 ]] || { echo "Cannot inspect managed data disk (blkid exit ${code})."; exit 1; }
  [[ "$(lsblk --noheadings --raw --output TYPE "${device}")" == "disk" ]] \
    || { echo "Refusing to format a disk with partitions."; exit 1; }
  [[ -z "$(wipefs --noheadings --output TYPE "${device}")" ]] \
    || { echo "Refusing to format a disk with existing signatures."; exit 1; }
  [[ -z "$(lsblk --noheadings --raw --output MOUNTPOINTS "${device}" | tr -d '[:space:]')" ]] \
    || { echo "Refusing to format a mounted disk."; exit 1; }
  mkfs.ext4 -q -m 1 -L orders-data "${device}"
fi
disk_uuid=$(blkid -s UUID -o value "${device}")
[[ -n "${disk_uuid}" ]] || { echo "Data disk UUID is empty."; exit 1; }
mkdir -p /var/lib/orders
existing_source=$(awk '$2 == "/var/lib/orders" { print $1 }' /etc/fstab)
if [[ -n "${existing_source}" && "${existing_source}" != "UUID=${disk_uuid}" ]]; then
  echo "Refusing to replace the existing /var/lib/orders mount."
  exit 1
fi
if [[ -z "${existing_source}" ]]; then
  printf 'UUID=%s /var/lib/orders ext4 defaults,nofail,nodev,nosuid,noexec,x-systemd.device-timeout=30s 0 2\n' \
    "${disk_uuid}" >> /etc/fstab
fi
systemctl daemon-reload
if ! mountpoint --quiet /var/lib/orders; then
  mount /var/lib/orders
fi
[[ "$(findmnt --noheadings --output UUID --target /var/lib/orders)" == "${disk_uuid}" ]] \
  || { echo "The Orders directory is not mounted from LUN 0."; exit 1; }

start_stage packages
if ! command -v dotnet >/dev/null || ! dotnet --list-sdks | grep --quiet '^8\.' \
    || ! command -v sqlite3 >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  timeout 180 apt-get update -qq -o Acquire::Retries=2 -o Acquire::http::Timeout=30
  timeout 240 apt-get install --yes --no-install-recommends -qq \
    -o DPkg::Lock::Timeout=60 dotnet-sdk-8.0 sqlite3 curl
fi
if ! id orders >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/orders-api --shell /usr/sbin/nologin orders
fi
chown orders:orders /var/lib/orders /var/lib/orders-api
chmod 0750 /var/lib/orders /var/lib/orders-api
install -d -m 0755 /opt/orders-api /opt/orders-api/releases
install -m 0644 "${work}/vm/faults.py" /opt/orders-api/faults.py
install -m 0644 "${work}/vm/inspect.py" /opt/orders-api/inspect.py

start_stage application-build
release="/opt/orders-api/releases/${digest}"
if [[ ! -f "${release}/OrdersApi.dll" ]]; then
  export HOME=/root DOTNET_CLI_HOME=/root
  export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
  timeout 240 dotnet publish "${work}/app/OrdersApi.csproj" --configuration Release \
    --runtime linux-x64 --self-contained false --output "${work}/publish" --nologo --verbosity minimal
  [[ -f "${work}/publish/OrdersApi.dll" ]] || { echo "Publish did not produce OrdersApi.dll."; exit 1; }
  mv "${work}/publish" "${release}"
  chmod -R go-w "${release}"
fi

start_stage sqlite-and-service
/usr/bin/python3 - "${settings}" <<'PY'
import base64
import json
from pathlib import Path
import sys

settings = json.loads(base64.b64decode(sys.argv[1]))
connection = settings["applicationInsightsConnectionString"]
if any(character in connection for character in ('"', "\n", "\r", "\\")):
    raise ValueError("Invalid Application Insights connection string")
Path("/etc/orders-api.env").write_text(
    "ASPNETCORE_URLS=http://0.0.0.0:8080\n"
    "ASPNETCORE_ENVIRONMENT=Production\n"
    'ConnectionStrings__OrdersDb="Data Source=/var/lib/orders/orders.db"\n'
    f'APPLICATIONINSIGHTS_CONNECTION_STRING="{connection}"\n'
    "SERVICE_NAME=orders-api\n"
    "DOTNET_CLI_TELEMETRY_OPTOUT=1\n"
    "TMPDIR=/var/lib/orders-api\n",
    encoding="utf-8",
)
PY
chown root:orders /etc/orders-api.env
chmod 0640 /etc/orders-api.env
timeout 60 runuser -u orders -- env \
  'ConnectionStrings__OrdersDb=Data Source=/var/lib/orders/orders.db' \
  DOTNET_CLI_TELEMETRY_OPTOUT=1 /usr/bin/dotnet "${release}/OrdersApi.dll" --bootstrap
ln -sfn "${release}" /opt/orders-api/current.next
mv --force --no-target-directory /opt/orders-api/current.next /opt/orders-api/current
install -m 0644 "${work}/vm/orders-api.service" /etc/systemd/system/orders-api.service
systemctl daemon-reload
systemctl enable orders-api
systemctl restart orders-api
ready=false
for _ in {1..24}; do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8080/health/ready; then
    ready=true
    break
  fi
  if systemctl is-failed --quiet orders-api; then
    echo "Orders API entered a failed state."
    exit 1
  fi
  sleep 5
done
[[ "${ready}" == "true" ]] || { echo "Orders API readiness timed out."; exit 1; }
printf '%s\n' "${digest}" > /opt/orders-api/bundle.sha256
timings="${timings}\"${stage_name}\":$((SECONDS - stage_started))"
echo
/usr/bin/python3 /opt/orders-api/inspect.py "${request_id}" --stages "{${timings}}"
