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
# Uses the selected azd environment and retrieves the fault credential from Key Vault in memory.

set -euo pipefail

exec python3 "$(dirname "${BASH_SOURCE[0]}")/workshop.py" fault "$@"
