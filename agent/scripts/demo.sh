#!/usr/bin/env bash
# Short, visible run for recording the walkthrough video: three requirements of increasing
# difficulty (the same tiers as eval/EVAL_RESULTS.md), in a visible, slowed-down browser.
#
# Usage (from agent/, with GEMINI_API_KEY loaded):
#   scripts/demo.sh sample_run/20261021-092147/requirements.json
#   (ids in that file: ACC-01 easy, TXN-03 medium, TRF-04 hard = overdraft)
#
# Passing a baseline requirements.json keeps requirement ids stable and skips the INTERPRET call.
# Without it, the run starts by reading the PRD (good to show "spec ingestion" on camera).
set -euo pipefail
cd "$(dirname "$0")/.."

ONLY="${DEMO_ONLY:-ACC-01,TXN-03,TRF-04}"
EXTRA=()
if [[ $# -ge 1 ]]; then EXTRA=(--requirements "$1"); fi

exec .venv/bin/qa-agent run \
  --prd ../test-website/PRD.md \
  --url "${TARGET_URL:-http://localhost:5173}" \
  --repo ../test-website/frontend/src \
  --only "$ONLY" \
  --headed --slow-mo "${SLOW_MO:-350}" \
  "${EXTRA[@]}"
