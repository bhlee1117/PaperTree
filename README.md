# PaperTree

A literature atlas built around claims rather than around papers.

Most reference managers answer "what have I read?". This answers "what does the field
claim, what holds it up, and where is it thin?" — and it answers the second part
mechanically, from the labels, so you find out that a claim rests entirely on one
preparation without having to notice it yourself.

Visit, https://bhlee1117.github.io/PaperTree/ for the latest status of the tree.

---

## The idea in one table

There are two databases here, and they are different in kind.

| | holds | written by | changes when |
|---|---|---|---|
| `papers.json` | **facts** — authors, year, preparation, measurement | the pipeline | Zotero changes |
| `claims.yaml` | **interpretation** — what the field asserts | **you** | your thinking changes |

They are joined by **edges**: this paper *supports / qualifies / contradicts / assumes*
this claim. That join is the whole tool.

The rule that falls out: **facts go in labels, interpretation goes in claims.** An early
version had a `view` axis on each paper (dendritic-local vs bAP-centric). It was retired,
because that is an interpretation, and as a claim it is strictly better represented — a
claim carries a stance, a strength, a condition, and contradicting evidence. A label
carries none of those.

---

## Setup, once

```bash
cd ~/Documents/GitHub/PaperTree
chmod +x *.sh
./setup.sh
```

That creates `.venv`, installs `requests` and `pyyaml`, and writes a `.env`. Open `.env`
and paste your key:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENALEX_MAILTO=you@example.edu     # optional, gets you OpenAlex's faster pool
ATLAS_LANG=English                  # or Korean — language of the significance line
```

`.env` is gitignored. It must stay that way; see **Publishing** below.

Downloaded files arrive without the execute bit, so `chmod +x *.sh` again after replacing
any script.

---

## Daily use

```bash
# 1. In Zotero: right-click the collection → Export Collection → CSV → save here
./update.sh --check     # what would this cost? changes nothing
./update.sh             # do it
open papertree.html
```

`update.sh` runs the whole pipeline and pays only for what actually changed. Re-running it
with nothing changed costs **$0.00** and makes zero API calls.

`papertree.html` has your data baked inside it. Nothing to drag in, and it works offline —
mail it to yourself, AirDrop it to your phone.

---

## Adding papers: export the whole collection, every time

**Yes, the CSV must include the papers you already had.** Export the entire collection,
not just the new ones.

Cached papers cost nothing, so a full export is not wasteful — matching is by DOI, so
re-exporting the same items is recognised and skipped. What a partial export does instead
is worse than expensive:

```
exported ONLY the 1 new paper
  0 cached · 1 new · 14 not in this export (archived, not discarded)
  papers.json live: 1 · archived: 14
```

Papers absent from an export move to an `archive` section. They are not deleted and come
back free when you export them again — but while archived they are not in the atlas, so
the viewer would show exactly one paper. **The archive is a safety net against losing paid
work, not a workflow.**

So: one collection in Zotero, exported whole. Adding three papers costs three labelling
calls and three claim-assignment calls, roughly $0.05.

### Papers without a DOI

Those match on a normalised title. If the title changes at all — a fixed typo, a metadata
refresh from the publisher — the cache misses and the paper is relabelled. Adding the DOI
in Zotero fixes it permanently.

---

## Editing claims

`claims.yaml` is the part you write. Everything else exists to serve it.

```bash
python3 check_claims.py            # validate, and show inherited caveats
python3 check_claims.py --weak     # claims that look stronger than they are
./update.sh                        # re-assigns only what changed
```

Work is tracked per **(paper, claim)** pair. Each paper stores `claims_seen`, a map from
claim id to a hash of that claim's text and note. Add a claim, or reword one, and only
that claim is re-evaluated against the library.

**Batch your claim edits.** The per-paper floor is the abstract, re-sent whatever the menu
size. Five claims added one at a time costs about $2.50; the same five added in one run
costs about $0.65.

| you changed | what re-runs |
|---|---|
| added a claim | that claim, against every in-scope paper |
| reworded a claim or its note | that claim, against every in-scope paper |
| added papers in Zotero | all claims, against those papers only |
| edited an evidence list by hand in `claims.yaml` | nothing — human edges are never touched |

Papers matching no claim land in a queue. A growing queue means the tree is missing a node
— read a few and write one:

```bash
python3 assign_claims.py --queue
```

That judgement is not delegated. The model may only choose from existing claim ids; it
cannot invent a claim.

---

## Cost, measured on a 270-paper library

The claim menu and the rules are identical across papers, so they go in a cached prompt
prefix and only the abstract is billed at full rate.

| | |
|---|---|
| first full build (labels + claims) | ~$3.50 |
| re-run, nothing changed | **$0.00** |
| add 3 papers | ~$0.05 |
| add one claim | ~$0.50 |
| add five claims in one run | ~$0.65 |

`assign_claims.py` prints the real billed cost and the cache hit rate at the end, so you
can check this rather than trust the estimate.

---

## The viewer

`papertree.html`, three tabs.

**Claim tree.** Seven questions on the left. Pick one and its claims fan out; pick a claim
and its papers appear. Each claim shows four diagnostic cards, and the one that matters is
**thin evidence** — flagged when a claim's positive case rests on one direct source, or
when every supporting paper shares a single preparation or modality. That second test is
the entire reason the factual label axes were kept.

Caveats are **computed, not annotated**. `meta.caveat_rules` in `claims.yaml` maps a
modality onto the methodological claims in Q7, so a claim whose support is all calcium
imaging automatically inherits "calcium reports suprathreshold electrogenesis, not
membrane potential". Adding a caveat later is one line, not an edit to every edge.

**Author–paper network.** Year runs down the page; each paper hangs from its most
author-similar predecessor inside a year window. Papers with no such predecessor are roots
— a new line, or a gap in your library. Two sliders retune the similarity floor and the
window live.

**All papers.** Faceted browser. Filter by any label axis, phenomena, method flags, author
family, year, and status (on a contested claim, on a thin claim, no claim attached, needs
review). Counts are computed with each facet's own filter lifted, so a number tells you
what selecting it would give you. `Copy DOIs` puts the filtered set on the clipboard,
ready for Zotero's *Add Item by Identifier*.

Clicking any paper expands it: labels with confidence, abstract, full author list with
first and last in bold, attached claims with the quote that justified each, and nearest
neighbours by weighted authorship.

Dim rows marked `not in Zotero` are papers cited by `claims.yaml` but absent from your
library. `build_atlas.py` writes them to `missing_papers.txt` as a reading list.

---

## Publishing

Not part of `update.sh`, on purpose. `update.sh` is the command you run constantly; a
half-finished state should not reach a public URL because you rebuilt.

```bash
./publish.sh          # shows what would go public, changes nothing
./publish.sh --go     # copies into docs/, commits, pushes
```

Then once, on github.com: **Settings → Pages → Source: Deploy from a branch → Branch:
main, Folder: /docs → Save.**

**On a public repo, Pages does not publish a page — it publishes the repository.**
`claims.yaml` and `papers.json` are tracked, and they contain your reading of which claims
are thin and where your own work sits as contested evidence, with every past version in
the history. `publish.sh` prints the repo's visibility, lists what would become public, and
refuses outright if `.env` is in the history.

For a URL without making it public, `PUBLISH.md` covers Cloudflare Pages with Access —
free, and it puts an email gate in front.

One command end to end:

```bash
alias ptpub='cd ~/Documents/GitHub/PaperTree && ./update.sh && ./publish.sh --go'
```

An alias rather than a script, so the intent to publish is visible in what you type.

---

## Files

| | |
|---|---|
| `claims.yaml` | **the spine.** Hand-written. Everything else serves it |
| `build_db.py` | Zotero → metadata, factual labels, author clusters |
| `assign_claims.py` | attaches papers to claims. Re-runnable on its own |
| `build_atlas.py` | merges both sources, computes diagnostics, bakes the page |
| `check_claims.py` | validates `claims.yaml`, reports inherited caveats and thin claims |
| `atlas_template.html` | the viewer source. `papertree.html` is built from it |
| `update.sh` | runs the three stages |
| `publish.sh` | GitHub Pages |
| `serve.py` | serve locally without exposing `.env` — see `PUBLISH.md` |
| `papers.json` | facts, machine edges, archive. **Commit this** |
| `atlas.json` | merged output the viewer reads |
| `missing_papers.txt` | cited by claims, absent from Zotero. A reading list |

Longer docs: `PIPELINE.md` (how the stages fit together), `MAINTAINING.md` (running it
over time), `PUBLISH.md` (sharing), `SETUP.md` (first install).

---

## Provenance

| | |
|---|---|
| edges in `claims.yaml` | `source: human` — never overwritten |
| edges in `papers.json` | `source: model` — replaced on `--reassign` |
| labels fixed in the viewer | `human_verified` — survive even a taxonomy change |

Where both describe the same paper–claim pair, the human edge wins and the model edge is
dropped at merge time. The viewer marks each edge with its source.

---

## A note on caching, for whoever maintains this next

Four separate bugs here had the same shape. Each asked *"has this been done?"* when the
question was *"what was done?"*:

- two label axes were retired; every paper still counted as labelled, so the run reported
  "0 papers to label" while the database held answers to questions that no longer existed
- a claim was added; every paper was marked finished, so the new node got nothing
- the output language changed; the axes were identical, so nothing was regenerated
- the fingerprints themselves were not carried through the merge, so every run re-paid for
  the entire library

All four are fixed by storing a fingerprint of the **output** rather than a boolean:
`label_fp` (axes + values + language) and `claims_seen` (claim id → text hash). Both are in
`CARRY`, guarded by an assertion, because leaving them out is exactly what the fourth bug
was.

If you add a stage that remembers its own work, give it a fingerprint and add it to `CARRY`
in the same commit.

---

## What is not automated

Labelling and claim assignment are machine work. Clustering is arithmetic. But
`claims.yaml` — what the field asserts, and where it splits — is yours to write.

Ask a model to extract claims from 260 papers and you get 700 near-duplicates; merging
those is harder than the labelling was. The tree is already in your head — it is what goes
in a paper's introduction. Writing it down is the actual intellectual work here. Everything
else is plumbing that makes it cheap to keep.
