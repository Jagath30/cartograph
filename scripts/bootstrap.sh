#!/usr/bin/env bash
set -euo pipefail

# Run from anywhere; operate on the repository root.
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo "bootstrap: .env already exists, leaving it alone"
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "bootstrap: openssl not found, cannot generate passwords" >&2
  exit 1
fi

cp .env.example .env

# Hex only, so there is nothing to escape in a connection URL or in a SQL
# string literal (NFR-09: the generated file is gitignored, never committed).
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
sed -i "s|^WAREHOUSE_RO_PASSWORD=.*|WAREHOUSE_RO_PASSWORD=$(openssl rand -hex 24)|" .env

echo "bootstrap: wrote .env with generated passwords"
