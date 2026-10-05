#!/usr/bin/env bash
# Runs bounded workshop faults through authenticated Azure control-plane operations.
#
# Usage:
#   ./scripts/inject-fault.sh status
#   ./scripts/inject-fault.sh cpu  [seconds] [workers]
#   ./scripts/inject-fault.sh postgresql
#   ./scripts/inject-fault.sh reset-cpu
#   ./scripts/inject-fault.sh reset-postgresql
#   ./scripts/inject-fault.sh reset  # Explicit all-scenarios cleanup

set -euo pipefail

exec python3 "$(dirname "${BASH_SOURCE[0]}")/workshop.py" fault "$@"
