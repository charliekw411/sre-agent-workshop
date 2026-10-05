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

start_stage packages
if ! command -v dotnet >/dev/null || ! dotnet --list-sdks | grep --quiet '^8\.' \
    || ! command -v psql >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  timeout 180 apt-get update -qq -o Acquire::Retries=2 -o Acquire::http::Timeout=30
  timeout 240 apt-get install --yes --no-install-recommends -qq \
    -o DPkg::Lock::Timeout=60 dotnet-sdk-8.0 postgresql-client curl ca-certificates
fi
if ! id orders >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/contoso-orders --shell /usr/sbin/nologin orders
fi
install -d -o orders -g orders -m 0750 /var/lib/contoso-orders
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

start_stage postgresql-configuration
/usr/bin/python3 - "${settings}" <<'PY'
import base64
import json
from pathlib import Path
import re
import sys

settings = json.loads(base64.b64decode(sys.argv[1]))
connection = settings["applicationInsightsConnectionString"]
host = settings["postgresqlHost"]
database = settings["postgresqlDatabase"]
user = settings["postgresqlUser"]
if not re.fullmatch(r"[A-Za-z0-9=;:/?._-]+", connection):
    raise ValueError("Invalid Application Insights connection string")
if not re.fullmatch(r"[a-z0-9-]+\.postgres\.database\.azure\.com", host):
    raise ValueError("Invalid PostgreSQL private host")
for label, value in (("database", database), ("user", user)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", value):
        raise ValueError(f"Invalid PostgreSQL {label}")
Path("/etc/orders-api.env").write_text(
    "ASPNETCORE_URLS=http://0.0.0.0:8080\n"
    "ASPNETCORE_ENVIRONMENT=Production\n"
    f'ConnectionStrings__OrdersDb="Host={host};Port=5432;Database={database};'
    f'Username={user};SSL Mode=VerifyFull;Timeout=5;Command Timeout=5;'
    'Application Name=orders-api"\n'
    "OrdersDatabase__Authentication=ManagedIdentity\n"
    f'APPLICATIONINSIGHTS_CONNECTION_STRING="{connection}"\n'
    "SERVICE_NAME=orders-api\n"
    "DOTNET_CLI_TELEMETRY_OPTOUT=1\n"
    "TMPDIR=/var/lib/contoso-orders\n",
    encoding="utf-8",
)
PY
chown root:orders /etc/orders-api.env
chmod 0640 /etc/orders-api.env

postgresql_host=$(/usr/bin/python3 - "${settings}" <<'PY'
import base64
import json
import sys
print(json.loads(base64.b64decode(sys.argv[1]))["postgresqlHost"])
PY
)
dns_ready=false
for _ in {1..60}; do
  if getent ahostsv4 "${postgresql_host}" | grep --quiet 'STREAM'; then
    dns_ready=true
    break
  fi
  sleep 5
done
[[ "${dns_ready}" == "true" ]] || { echo "PostgreSQL private DNS did not become ready."; exit 1; }

tcp_ready=false
for _ in {1..60}; do
  if timeout 5 bash -c "exec 3<>/dev/tcp/${postgresql_host}/5432"; then
    tcp_ready=true
    break
  fi
  sleep 5
done
[[ "${tcp_ready}" == "true" ]] || { echo "PostgreSQL TCP 5432 did not become reachable."; exit 1; }

token_json=$(curl --fail --silent --show-error --max-time 10 \
  --noproxy '*' \
  --header Metadata:true \
  'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fossrdbms-aad.database.windows.net')
/usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("access_token") and value.get("expires_on")' \
  <<<"${token_json}"
unset token_json

start_stage postgresql-bootstrap
bootstrapped=false
for attempt in {1..30}; do
  if timeout 70 runuser -u orders -- /bin/bash -c \
      "set -a; source /etc/orders-api.env; exec /usr/bin/dotnet '${release}/OrdersApi.dll' --bootstrap"; then
    bootstrapped=true
    break
  fi
  echo "PostgreSQL bootstrap not ready (attempt ${attempt}/30); retrying."
  sleep 10
done
[[ "${bootstrapped}" == "true" ]] || { echo "PostgreSQL bootstrap did not complete."; exit 1; }

start_stage service
ln -sfn "${release}" /opt/orders-api/current.next
mv --force --no-target-directory /opt/orders-api/current.next /opt/orders-api/current
install -m 0644 "${work}/vm/orders-api.service" /etc/systemd/system/orders-api.service
systemctl daemon-reload
systemctl enable orders-api
systemctl restart orders-api
ready=false
for _ in {1..36}; do
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
