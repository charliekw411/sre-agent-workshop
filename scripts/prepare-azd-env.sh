#!/usr/bin/env bash
set -euo pipefail

if ! azd env get-value SQL_ADMIN_PASSWORD >/dev/null 2>&1; then
  sql_admin_password="$(openssl rand -hex 16)Aa1!"
  azd env set SQL_ADMIN_PASSWORD "${sql_admin_password}"
fi

if ! azd env get-value FAULT_TOKEN >/dev/null 2>&1; then
  fault_token="$(openssl rand -hex 24)"
  azd env set FAULT_TOKEN "${fault_token}"
fi

if ! azd env get-value ALERT_EMAIL >/dev/null 2>&1; then
  alert_email="$(az ad signed-in-user show --query mail --output tsv)"
  if [[ -z "${alert_email}" || "${alert_email}" == "null" ]]; then
    alert_email="$(az ad signed-in-user show --query userPrincipalName --output tsv)"
  fi
  azd env set ALERT_EMAIL "${alert_email}"
fi