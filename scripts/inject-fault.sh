#!/usr/bin/env bash
# Runs bounded workshop faults through authenticated Azure VM Run Command.
#
# Usage:
#   ./scripts/inject-fault.sh status
#   ./scripts/inject-fault.sh cpu  [seconds] [workers]
#   ./scripts/inject-fault.sh disk [target-percent] [seconds]
#   ./scripts/inject-fault.sh reset

set -euo pipefail

exec python3 "$(dirname "${BASH_SOURCE[0]}")/workshop.py" fault "$@"
