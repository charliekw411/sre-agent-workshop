#!/usr/bin/env bash
# Generates steady baseline traffic against orders-api so incidents have something to disrupt.
#
# Usage:
#   ./scripts/generate-load.sh <orders-api-fqdn> [requests-per-second] [duration-seconds]
#
# Example:
#   ./scripts/generate-load.sh orders-api.happyocean-1234.eastus.azurecontainerapps.io 5 900

set -euo pipefail

FQDN="${1:-${ORDERS_API_FQDN:-}}"
RATE="${2:-5}"
DURATION="${3:-900}"

if [[ -z "${FQDN}" ]]; then
  echo "Usage: $0 <orders-api-fqdn> [requests-per-second] [duration-seconds]" >&2
  echo "Or export ORDERS_API_FQDN before running." >&2
  exit 1
fi

BASE_URL="https://${FQDN}"
PRODUCTS=("SKU-1001" "SKU-1002" "SKU-1003" "SKU-1004" "SKU-1005")
INTERVAL=$(awk -v r="${RATE}" 'BEGIN { printf "%.3f", 1 / r }')

echo "Target      : ${BASE_URL}"
echo "Rate        : ${RATE} requests/second"
echo "Duration    : ${DURATION} seconds"
echo "Press Ctrl+C to stop early."
echo

START=$(date +%s)
SENT=0
SUCCESS=0
CLIENT_ERROR=0
SERVER_ERROR=0

print_summary() {
  local elapsed=$(( $(date +%s) - START ))
  echo
  echo "----------------------------------------"
  echo "Elapsed          : ${elapsed}s"
  echo "Requests sent    : ${SENT}"
  echo "2xx responses    : ${SUCCESS}"
  echo "4xx responses    : ${CLIENT_ERROR}"
  echo "5xx responses    : ${SERVER_ERROR}"
  echo "----------------------------------------"
}

trap print_summary EXIT

while (( $(date +%s) - START < DURATION )); do
  PRODUCT="${PRODUCTS[$((RANDOM % ${#PRODUCTS[@]}))]}"
  QUANTITY=$((RANDOM % 5 + 1))
  CUSTOMER="cust-$((RANDOM % 500))"

  STATUS=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --max-time 15 \
    --request POST "${BASE_URL}/orders" \
    --header 'Content-Type: application/json' \
    --data "{\"customerId\":\"${CUSTOMER}\",\"productId\":\"${PRODUCT}\",\"quantity\":${QUANTITY}}" \
    || echo "000")

  SENT=$((SENT + 1))
  case "${STATUS}" in
    2*) SUCCESS=$((SUCCESS + 1)) ;;
    4*) CLIENT_ERROR=$((CLIENT_ERROR + 1)) ;;
    *)  SERVER_ERROR=$((SERVER_ERROR + 1)) ;;
  esac

  if (( SENT % 25 == 0 )); then
    printf 'sent=%-6d 2xx=%-6d 4xx=%-6d 5xx=%-6d last=%s\n' \
      "${SENT}" "${SUCCESS}" "${CLIENT_ERROR}" "${SERVER_ERROR}" "${STATUS}"
  fi

  sleep "${INTERVAL}"
done
