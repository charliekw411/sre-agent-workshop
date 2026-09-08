#!/usr/bin/env bash
# Triggers, inspects, and clears the workshop fault-injection endpoints on orders-api.
#
# Usage:
#   ./scripts/inject-fault.sh status
#   ./scripts/inject-fault.sh cpu     [seconds] [threads]
#   ./scripts/inject-fault.sh errors  [rate-percent] [ttl-seconds]
#   ./scripts/inject-fault.sh storage [target-percent]
#   ./scripts/inject-fault.sh release
#   ./scripts/inject-fault.sh reset
#
# Requires ORDERS_API_FQDN and FAULT_TOKEN, normally sourced from .workshop/workshop.env.

set -euo pipefail

: "${ORDERS_API_FQDN:?Set ORDERS_API_FQDN, or run: source .workshop/workshop.env}"
: "${FAULT_TOKEN:?Set FAULT_TOKEN, or run: source .workshop/workshop.env}"

BASE_URL="https://${ORDERS_API_FQDN}"
ACTION="${1:-status}"

post() {
  curl --silent --show-error --fail-with-body \
    --request POST "${BASE_URL}/fault/$1" \
    --header 'Content-Type: application/json' \
    --header "X-Fault-Token: ${FAULT_TOKEN}" \
    --data "$2"
  echo
}

case "${ACTION}" in
  status)
    curl --silent --show-error --fail-with-body \
      --header "X-Fault-Token: ${FAULT_TOKEN}" \
      "${BASE_URL}/fault/status" | jq .
    ;;
  cpu)
    SECONDS_ARG="${2:-600}"
    THREADS_ARG="${3:-4}"
    echo "Injecting CPU load: ${THREADS_ARG} threads for ${SECONDS_ARG}s"
    post "cpu" "{\"seconds\":${SECONDS_ARG},\"threads\":${THREADS_ARG}}"
    ;;
  errors)
    RATE_ARG="${2:-100}"
    TTL_ARG="${3:-900}"
    echo "Injecting catalog dependency failures: ${RATE_ARG}% for ${TTL_ARG}s"
    post "errors" "{\"ratePercent\":${RATE_ARG},\"ttlSeconds\":${TTL_ARG}}"
    ;;
  storage)
    TARGET_ARG="${2:-95}"
    echo "Filling the orders database to ${TARGET_ARG}% of its maximum size"
    post "storage" "{\"targetPercent\":${TARGET_ARG}}"
    ;;
  release)
    echo "Releasing database ballast and shrinking the database"
    post "storage/release" '{}'
    ;;
  reset)
    echo "Clearing all injected faults"
    post "reset" '{}'
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    echo "Valid actions: status, cpu, errors, storage, release, reset" >&2
    exit 1
    ;;
esac
