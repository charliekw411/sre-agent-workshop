#!/usr/bin/env bash
set -euo pipefail

mkdir -p .workshop
azd env get-values | sed '/^[[:space:]]*$/d; s/^/export /' > .workshop/workshop.env
chmod 600 .workshop/workshop.env