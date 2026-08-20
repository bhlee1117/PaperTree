#!/usr/bin/env bash
# setup.sh — one-time setup. Run once, then only ever use ./update.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "Creating a virtual environment in .venv ..."
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet requests pyyaml
echo "  installed: requests, pyyaml"

if [[ ! -f .env ]]; then
  cat > .env <<'ENVEOF'
# Your Anthropic API key. This file is gitignored — never commit it.
ANTHROPIC_API_KEY=

# Optional: your email gets you OpenAlex's faster "polite pool".
OPENALEX_MAILTO=
ENVEOF
  chmod 600 .env
  echo "  created .env — put your API key in it"
fi

chmod +x update.sh
echo
echo "Done. Next:"
echo "  1. Put your key in .env"
echo "  2. Export your Zotero collection as CSV into this folder"
echo "  3. ./update.sh --check"
