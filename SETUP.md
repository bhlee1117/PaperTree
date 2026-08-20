# Setup on your Mac

Everything lives in
`/Users/bhlee1117/Documents/GitHub/Cohen_lab_BHL_Code/BHL_Python/PaperDB/`

## One time

```bash
cd ~/Documents/GitHub/Cohen_lab_BHL_Code/BHL_Python/PaperDB
chmod +x setup.sh update.sh
./setup.sh
```

That makes a `.venv`, installs `requests`, and writes a `.env`. Open `.env` and
paste your key:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENALEX_MAILTO=bhlee1117@fas.harvard.edu
```

## Every time

```bash
cd ~/Documents/GitHub/Cohen_lab_BHL_Code/BHL_Python/PaperDB

# Zotero: right-click the collection -> Export Collection -> CSV -> save into this folder
./update.sh --check     # what would it cost? changes nothing
./update.sh             # do it
open dendrite_atlas.html
```

Then drag `papers.json` onto the page, tick **only flagged for review**, and work
through the new ones.

No `source .venv/bin/activate` needed — `update.sh` finds the venv itself, which is
also what lets it run from cron later. With no filename argument it picks the most
recently modified `.csv` or `.bib` in the folder, so you can just re-export from
Zotero over the top and re-run.

## Because this is a git repo

`.gitignore` keeps `.env` out of version control. **A key pushed to GitHub even once
is compromised** — GitHub is scraped for them continuously. If it ever happens, rotate
it at console.anthropic.com immediately rather than deleting the commit.

Also ignored: `.venv/`, `backups/`, and the Zotero exports themselves.

Deliberately **tracked**: `papers.json` and `gold.json`. Those hold your hand-verified
labels, your notes, and your calibration set — the part that took real time. Commit
`papers.json` after a verification session:

```bash
git add papers.json && git commit -m "verify 30 new dendrite papers"
```

That also gives you a second safety net beyond `backups/`, with a readable history of
how your labels changed.

## Automatic weekly refresh, once you trust it

```bash
crontab -e
# 0 9 * * 1  cd /Users/bhlee1117/Documents/GitHub/Cohen_lab_BHL_Code/BHL_Python/PaperDB && ./update.sh >> update.log 2>&1
```

Pair it with Better BibTeX's auto-export so the `.bib` stays current without you
touching Zotero. Read `update.log` before trusting a run you did not watch.

## If something breaks

| symptom | cause |
|---|---|
| `ANTHROPIC_API_KEY is not set` | key missing from `.env`, or a stray space around the `=` |
| `No .csv or .bib export found here` | export from Zotero into this folder, or pass the path |
| `OpenAlex returned nothing for any DOI` | network or API down; it falls back to Zotero's fields and clustering gets weaker. Re-run later |
| lots of `SKIP no abstract` | expected — 34 of your items have none. Never labelled from a title alone |
| `command not found: python3` | install the Xcode command line tools: `xcode-select --install` |
