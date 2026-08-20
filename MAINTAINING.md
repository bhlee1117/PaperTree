# Maintaining the atlas yourself

## The routine

```bash
# 1. In Zotero: right-click the collection -> Export Collection -> CSV
# 2. Then:
./update.sh Dendrite.csv --check     # what would this cost? changes nothing
./update.sh Dendrite.csv             # do it
# 3. Open dendrite_atlas.html, drop in papers.json,
#    tick "only flagged for review", work through the new ones.
```

That is the whole loop. Run it whenever you like — monthly, or after a reading session.

## Why re-running is cheap

`--merge` matches each Zotero item against the existing `papers.json` by DOI (falling
back to a normalised title). Anything already labelled is copied forward untouched.
Only genuinely new papers reach the API.

Measured on your library:

| run | papers | API calls | cost |
|---|---|---|---|
| first build | 260 | 226 | ~$2.40 |
| re-run, nothing changed | 260 | 0 | $0 |
| after adding 4, deleting 2 | 262 | 4 | ~$0.04 |

**Your hand edits always win.** When you change a label in the viewer it sets
`human_verified`, and nothing — not a re-run, not `--relabel` — will overwrite it.
Your `note` field survives too. `update.sh` also keeps the last 10 backups in
`backups/`, because the valuable thing in that file is your verification hours,
not the API spend.

## When you change the taxonomy

Adding a value to an axis, or rewriting a definition, means old labels were chosen
under different rules. Re-label just that axis:

```bash
python3 build_db.py --zotero Dendrite.csv --merge papers.json --out papers.json \
        --relabel view
```

Papers you verified by hand are skipped. Everything else on that axis is redone;
the other axes are left alone.

## Catching silent drift

The failure you cannot see in the output: you tweak the prompt, or a model version
shifts underneath you, and labels move without anything looking broken.

Once you have hand-checked ~25 papers in the viewer, freeze them as a reference set:

```bash
python3 build_db.py --zotero Dendrite.csv --merge papers.json --make-gold
```

After that, `update.sh` re-labels those 25 from scratch on every run and prints
agreement per axis:

```
  agreement with your hand labels:
    scope                24/25 ███████████████████
    prep                 23/25 ██████████████████
    view                 17/25 █████████████  <-- drifted
```

Below 80% on an axis means the definition is genuinely ambiguous, not that the model
is broken. Expect `view` to be your weakest axis — it is an interpretive judgement,
and it is the one to rewrite when it slips.

## Things that will bite you

**Duplicates.** Your library has 10 duplicate pairs right now (same DOI entered
twice, or a preprint plus its published version). The pipeline merges them and lists
them on every run, but clean them up in Zotero so the count stops lying to you.

**Accented names.** Your export has both `Buzsaki` and `Buzsáki` as last authors —
two labs where there is one. Surnames are now accent-folded, and OpenAlex author IDs
fix it properly for the 247 items with a DOI. The 13 without a DOI stay fragile.

**No abstract, no label.** 34 of your items have no usable abstract (mostly the 15
`webpage` entries). These are never sent to the model — labelling from a title alone
is how you get confident wrong answers. They arrive flagged. Either paste an abstract
into Zotero, or label them by hand in the viewer.

**Cluster numbers are not stable.** Clustering is global, so it is recomputed from
scratch every run and `lab_cluster` integers shift. The cluster *names* (last-author
surnames) are the stable thing. Do not build anything that keys off the integer.

**If OpenAlex is unreachable**, the run says so loudly and falls back to Zotero's own
fields. Clustering gets weaker because author IDs are missing. Re-run later.

## Fully automatic, if you want it

Zotero's Better BibTeX add-on can auto-export a collection to a `.bib` file whenever
it changes. `build_db.py` reads `.bib`, and since metadata comes from OpenAlex anyway,
the export only really needs to carry DOIs. Point a weekly cron job at it:

```
0 9 * * 1  cd ~/litatlas && ./update.sh ~/Zotero/dendrite.bib >> update.log 2>&1
```

Read `update.log` before trusting a run you did not watch.
