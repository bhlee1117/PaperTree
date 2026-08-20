#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x .venv/bin/python3 ]]; then PY=.venv/bin/python3; else PY=python3; fi

DB="papers.json"; CHECK=""; CSV=""
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    *) CSV="$arg" ;;
  esac
done

if [[ -z "$CSV" ]]; then
  CSV=$(ls -1t ./*.csv ./*.bib 2>/dev/null | head -1 || true)
  if [[ -z "$CSV" ]]; then
    echo "No .csv or .bib export found here. Export from Zotero into $(pwd)." >&2
    exit 1
  fi
  echo "Using $CSV"
fi
[[ -f "$CSV" ]] || { echo "No such file: $CSV" >&2; exit 1; }

if [[ -z "${ANTHROPIC_API_KEY:-}" && -f .env ]]; then
  set -a; . ./.env; set +a
fi
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is not set. Put it in $(pwd)/.env" >&2
  exit 1
fi

if [[ -n "$CHECK" ]]; then
  exec "$PY" build_db.py --zotero "$CSV" --merge "$DB" --dry-run
fi

if [[ -f "$DB" ]]; then
  mkdir -p backups
  cp "$DB" "backups/papers-$(date +%Y%m%d-%H%M%S).json"
  ls -1t backups/papers-*.json 2>/dev/null | tail -n +11 | while IFS= read -r old; do
    rm -f "$old"
  done
fi

"$PY" build_db.py --zotero "$CSV" --merge "$DB" --out "$DB.tmp" --check-gold
mv "$DB.tmp" "$DB"

echo
echo "Done. Open dendrite_atlas.html and drop in $DB."
