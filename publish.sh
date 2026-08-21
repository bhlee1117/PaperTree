#!/usr/bin/env bash
# publish.sh — put the current build on GitHub Pages.
#
#     ./publish.sh          check what would be published, change nothing
#     ./publish.sh --go     do it
#
# Publishing a static page is easy. Deciding what you are publishing is the part worth
# a script: on a public repo, Pages does not just expose index.html, it exposes the
# whole repository and its entire history. papers.json and claims.yaml are tracked, and
# they hold your reading of which claims are weak and where your own work is contested.
# This script refuses to run silently on a public repo for that reason.

set -euo pipefail
cd "$(dirname "$0")"

SRC="papertree.html"
GO="${1:-}"

[[ -f "$SRC" ]] || { echo "$SRC not built yet — run ./update.sh" >&2; exit 1; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repository" >&2; exit 1; }

REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
[[ -n "$REMOTE" ]] || { echo "no 'origin' remote — create the repo on GitHub first" >&2; exit 1; }

SLUG=$(sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##' <<<"$REMOTE")
USER="${SLUG%%/*}"; REPO="${SLUG##*/}"
BRANCH=$(git rev-parse --abbrev-ref HEAD)

VIS="unknown"
if command -v gh >/dev/null 2>&1; then
  VIS=$(gh repo view "$SLUG" --json visibility -q .visibility 2>/dev/null || echo unknown)
fi

echo "repository   $SLUG   (branch $BRANCH)"
echo "visibility   $VIS"
echo "would publish"
echo "    docs/index.html   <- $SRC   ($(du -h "$SRC" | cut -f1), data baked in)"
[[ -f atlas.json ]] && echo "    docs/atlas.json   <- atlas.json  ($(du -h atlas.json | cut -f1), read live by the page)"
echo "url          https://$USER.github.io/$REPO/"
echo

# Everything already tracked becomes readable by anyone the moment the repo is public.
echo "already tracked in git, and public with it:"
git ls-files | grep -E '\.(json|yaml|md|py)$' | sed 's/^/    /' | head -14
echo
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "  !! .env IS TRACKED. Your API key is in the history. Stop, rotate the key at" >&2
  echo "     console.anthropic.com, then 'git rm --cached .env' before publishing." >&2
  exit 1
fi
echo "  .env is not tracked — good"
echo

case "$VIS" in
  PUBLIC)  echo "  This repo is PUBLIC. Publishing makes the page, the notes, and every past"
           echo "  commit readable by anyone. That includes claim notes about unpublished work." ;;
  PRIVATE) echo "  This repo is PRIVATE. Pages from a private repo needs a paid GitHub plan;"
           echo "  on a free account the enable step will fail. See PUBLISH.md for the"
           echo "  Cloudflare Pages route, which can sit behind an email gate for free." ;;
  *)       echo "  Could not read visibility (install 'gh', or check on github.com)."
           echo "  Confirm it before publishing." ;;
esac
echo

if [[ "$GO" != "--go" ]]; then
  echo "dry run. Re-run with --go when the above is what you want."
  exit 0
fi

mkdir -p docs
cp "$SRC" docs/index.html
# The page reads this if it is reachable, so a data-only change needs no new index.html.
[[ -f atlas.json ]] && cp atlas.json docs/atlas.json
touch docs/.nojekyll          # otherwise Jekyll eats files beginning with an underscore
git add docs/index.html docs/.nojekyll
[[ -f docs/atlas.json ]] && git add docs/atlas.json
git commit -m "publish atlas $(date +%Y-%m-%d)" || echo "  (nothing changed)"
git push origin "$BRANCH"

echo
echo "Pushed. One-time setup on github.com:"
echo "    Settings -> Pages -> Source: Deploy from a branch"
echo "    Branch: $BRANCH   Folder: /docs   -> Save"
echo
echo "Live in a minute or two at  https://$USER.github.io/$REPO/"
echo "After that, ./update.sh && ./publish.sh --go  refreshes it."
