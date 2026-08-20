#!/usr/bin/env bash
# update.sh — routine refresh of the atlas from a Zotero export.
#
#   ./update.sh Dendrite.csv          normal update
#   ./update.sh Dendrite.csv --check  show what it would cost, change nothing
#
# Safe to run as often as you like: papers already labelled are never re-sent,
# and labels you fixed in the viewer are never overwritten.

set -euo pipefail
cd "$(dirname "$0")"

CSV="${1:?usage: ./update.sh <zotero-export.csv> [--check]}"
DB="papers.json"
MODE="${2:-}"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is not set." >&2; exit 1
fi

if [[ "$MODE" == "--check" ]]; then
  python3 build_db.py --zotero "$CSV" --merge "$DB" --dry-run
  exit 0
fi

# Keep the last 10 versions. The expensive thing here is not the API spend, it is
# the hours of hand-verification stored in this file.
if [[ -f "$DB" ]]; then
  mkdir -p backups
  cp "$DB" "backups/papers-$(date +%Y%m%d-%H%M%S).json"
  ls -1t backups/papers-*.json | tail -n +11 | xargs -r rm --
fi

# Write to a temp file first so a crash mid-run cannot leave you with a truncated DB.
python3 build_db.py --zotero "$CSV" --merge "$DB" --out "$DB.tmp" --check-gold
mv "$DB.tmp" "$DB"

echo
echo "Done. Open dendrite_atlas.html and drop in $DB."
echo "Filter to 'only flagged for review' and work through the new ones."
