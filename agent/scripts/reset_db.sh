#!/usr/bin/env bash
# Restore the local DemoBank database to its seeded state (replacement for `make reset`,
# which assumes Docker). Re-applies the idempotent migration, then the seed, which
# truncates and re-inserts every DemoBank table.
#
# Usage: agent/scripts/reset_db.sh
# Env:   DEMOBANK_DATABASE_URL (default: the kit's local credentials)
set -euo pipefail

DB_URL="${DEMOBANK_DATABASE_URL:-postgresql://demobank:demobank_local@localhost:5432/demobank}"
DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../test-website/database" && pwd)"

PGOPTIONS="-c client_min_messages=warning" psql "$DB_URL" -v ON_ERROR_STOP=1 -q \
  -f "$DB_DIR/migrations/001_create_schema.sql" \
  -f "$DB_DIR/seed.sql"

echo "DemoBank database reset to seed state. (All demo sessions were cleared: enter the demo again.)"
