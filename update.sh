#!/usr/bin/env bash
# update.sh — refresh the atlas from a Zotero export.
#
#   ./update.sh                    use the newest .csv/.bib in this folder
#   ./update.sh Dendrite.csv       use a specific export
#   ./update.sh --check            show what it would cost, change nothing
#
# Safe to run as often as you like: papers already labelled are never re-sent to
# the API, and labels you fixed in the viewer are never overwritten.

set -euo pipefail
cd "$(dirname "$0")"

# Use the local venv if there is one, so this works from cron and from a plain
# Terminal without anyone having to remember to activate anything.
if [[ -x .venv/bin/python3 ]]; then
  PY=.venv/bin/python3
else
  PY=python3
fi

DB="papers.json"
CHECK=""
CSV=""
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    *) CSV="$arg" ;;
  esac
done

# Default to the most recently modified export in this folder.
if [[ -z "$CSV" ]]; then
  CSV=$(ls -1t ./*.csv ./*.bib 2>/dev/null | head -1 || true)
  if [[ -z "$CSV" ]]; then
    echo "No .csv or .bib export found here. Export your collection from Zotero" >&2
    echo "into $(pwd), or pass the path: ./update.sh /path/to/export.csv" >&2
    exit 1
  fi
  echo "Using $CSV (most recent export in this folder)"
fi
[[ -f "$CSV" ]] || { echo "No such file: $CSV" >&2; exit 1; }

# Key can live in a gitignored .env rather than your shell profile.
if [[ -z "${ANTHROPIC_API_KEY:-}" && -f .env ]]; then
  set -a; . ./.env; set +a
fi
# Output language for the one-sentence significance. Set ATLAS_LANG in .env to change
# it; changing it invalidates the cached prose, so those papers get relabelled.
LANG_OUT="${ATLAS_LANG:-English}"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is not set." >&2
  echo "Either add it to your ~/.zshrc, or put this in $(pwd)/.env :" >&2
  echo "    ANTHROPIC_API_KEY=sk-ant-..." >&2
  exit 1
fi

if [[ -n "$CHECK" ]]; then
  exec "$PY" build_db.py --zotero "$CSV" --merge "$DB" --lang "$LANG_OUT" --dry-run
fi

# Keep the last 10 versions. The expensive thing in this file is not the API
# spend, it is the hours of hand-verification stored in it.
if [[ -f "$DB" ]]; then
  mkdir -p backups
  cp "$DB" "backups/papers-$(date +%Y%m%d-%H%M%S).json"
  # BSD xargs on macOS has no -r, so do the pruning in the shell instead.
  ls -1t backups/papers-*.json 2>/dev/null | tail -n +11 | while IFS= read -r old; do
    rm -f "$old"
  done
fi

# Write to a temp file first, so a crash mid-run cannot leave a truncated DB.
"$PY" build_db.py --zotero "$CSV" --merge "$DB" --out "$DB.tmp" \
    --lang "$LANG_OUT" --check-gold
mv "$DB.tmp" "$DB"

# Stages 2 and 3 run through the same interpreter, which is the whole reason they are
# here: calling a bare `python3` picks the system one, which has neither requests nor
# pyyaml, and the failure appears three commands after the one that mattered.
if [[ -f claims.yaml ]]; then
  echo
  "$PY" assign_claims.py --papers "$DB" || exit 1
  echo
  "$PY" build_atlas.py "$DB" || exit 1
fi

echo
echo "Done. Open papertree.html — your data is already in it."
echo "Tick 'only flagged for review' and work through the new ones."
