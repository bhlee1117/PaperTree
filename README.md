# Dendrite Atlas — Zotero -> labelled, clustered paper database

## Workflow

Zotero: right-click your collection -> **Export Collection** -> **CSV** (or CSL JSON).

```bash
pip install requests
export ANTHROPIC_API_KEY=sk-...
export OPENALEX_MAILTO=your@email.harvard.edu     # optional, OpenAlex polite pool

python build_db.py --zotero dendrites.csv --out papers.json
```

Then open `dendrite_atlas.html` and drag `papers.json` onto it. Single file, no server.

Useful flags:

| flag | |
|---|---|
| `--no-label` | metadata + clusters only, no API calls — good for a first look |
| `--no-enrich` | trust Zotero's own fields, skip OpenAlex |
| `--tau 0.30` | author-similarity merge threshold. Higher = smaller, tighter labs |
| `--lang Korean` | language of the significance sentence |

## Why Zotero is the seed list, not the source of truth

Zotero's Abstract Note is often empty or truncated depending on how each item was
imported, and it has no reference list or citation count. So the pipeline takes the
DOIs and your tags from Zotero, then re-fetches everything else from OpenAlex.
Zotero's abstract is kept only as a fallback.

Items with no usable abstract (<120 chars) are **never labelled by the model** — they
get `prep: unclear`, confidence 0, and a review flag. Labelling from a title alone is
where confident-but-wrong entries come from.

## Taxonomy notes

Four single-choice axes (`article_type`, `prep`, `view`, `phenomenon_primary`) plus one
multi-choice axis (`phenomena`).

- `article_type` is split out from `prep` on purpose. A review of in vivo work has both
  a document type and a preparation; merging them means you can never ask for "all in
  vivo work, primary and review".
- `phenomena` is multi-label because a paper can report NMDA *and* Ca spikes. Multi-label
  axes cannot be hierarchy levels (a paper would appear in two branches), so they render
  as filter chips instead. `phenomenon_primary` is the single-choice version for the tree.
- `bAP` appears in both `view` and `phenomenon_primary`. That is fine — one is stance, one
  is the measured object — but they correlate, so a 3-level tree using both gives you
  mostly singleton leaves. Two levels plus chips reads better.

## Author similarity

Each author gets a weight by position:

| position | weight | why |
|---|---|---|
| last | 1.00 | in this field the last author *is* the lab |
| first | 0.85 | did the work; carries technique and question between labs |
| second-to-last | 0.45 | usually the collaborating PI |
| second | 0.35 | |
| middle | 0.12 | often courtesy or resource contributions |

Papers become weighted author vectors; similarity is **weighted cosine** (not weighted
Jaccard, which would penalise a 3-author paper for matching a 25-author consortium one).
Shared last author floors the similarity at 0.75.

Clustering is **average linkage**, not union-find. Union-find is single linkage and it
chains: A~B and B~C merge A with C even when A and C share nobody, which glues a whole
subfield into one blob. On the sample library, single linkage put Larkum inside Sakmann;
average linkage at tau=0.30 separates them.

`--tau` is the knob. Sweep it and look at the cluster sizes before trusting them.

## Lineage links

Someone who is first author on an early paper and last author on a later one has almost
certainly started their own lab. Author overlap will never connect those two groups, but
intellectually they are one lineage — usually what "papers from a similar group" means.
These are computed separately and listed in `meta.lineage` and per-paper `lineage`.

## Sample data

The 18 papers in `papers.sample.json` are hand-entered to make the viewer demonstrable.
Years, citation counts, and labels are not verified. Build your own with `build_db.py`.

## Cost

One API call per paper, roughly 1.5k input tokens. ~300 papers lands around $1-2 on
Sonnet. Above ~500, switch to the Message Batches API for half price.
