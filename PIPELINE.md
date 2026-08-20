# Pipeline

    Zotero export
        |  build_db.py         metadata + FACTUAL labels + author clustering
        v
    papers.json  <-------+
        |                |
        |  assign_claims.py    attaches papers to claims.yaml  (re-runnable alone)
        v                |
    papers.json ---------+
        |
        |  build_atlas.py      merges claims.yaml + papers.json
        v
    atlas.json  ->  dendrite_atlas.html

    claims.yaml   hand-authored. The spine. check_claims.py validates it.

## Commands

    ./update.sh                                        metadata + labels
    python3 assign_claims.py --dry-run                 cost of the claim stage
    python3 assign_claims.py                           attach papers to claims
    python3 assign_claims.py --queue                   papers that matched nothing
    python3 assign_claims.py --reassign C2.3b          redo one claim after editing it
    python3 check_claims.py --weak                     claims that look stronger than they are
    python3 build_atlas.py && open dendrite_atlas.html

## Why claim assignment is its own script

claims.yaml changes far more often than the taxonomy — every time you split a node or
sharpen a wording. Re-running assignment must not mean re-paying for labelling. Editing
a claim costs `--reassign C2.3b`, which touches only the papers on that node and never
touches an edge you wrote by hand.

## The taxonomy after the move to claims

Retired: `view`, `phenomenon_primary`. `view` became claim C3.1, which is strictly
better — it now carries stance, strength, condition, and contradicting evidence.
Keeping it in both places was duplication with the worse copy winning by default.

Kept, and all of them factual: `scope` (routes what enters claim assignment),
`article_type` (reviews are claim SOURCES, primary research is EVIDENCE),
`prep`, `modality`, `phenomena`, `method_flags`.

The rule that fell out: **facts go in labels, interpretation goes in claims.** Every
surviving axis can be read off a methods section. Nothing left requires guessing what
the authors believe.

`modality` and `method_flags` are load-bearing, not decoration. meta.caveat_rules in
claims.yaml maps them onto the Q7 methodological claims, so caveat inheritance is
computed rather than annotated. Adding a caveat later is one line, not 116 edge edits.

## Provenance

| | |
|---|---|
| edges in claims.yaml | `source: human` — never overwritten |
| edges in papers.json | `source: model` — replaced on `--reassign` |
| labels you fix in the viewer | `human_verified` — never overwritten |

Where both describe the same paper-claim pair, the human edge wins and the model edge
is dropped at merge time. The viewer marks each edge with its source.

## Reading the viewer

Claims mode is the default. Each claim shows its evidence grouped by stance, plus four
diagnostic cards. The one to look at is **thin evidence**, flagged when a claim's
positive case rests on one direct source, or when every supporting paper shares a single
preparation or modality. That second test is the entire reason the factual axes survived.

Library mode is the old paper browser, with an extra grouping by claims attached — the
fastest way to see which papers are carrying the tree and which are inert.


## What re-runs when you change something

Work is tracked per (paper, claim) pair, not per paper. Each paper stores `claims_seen`,
a map from claim id to a hash of that claim's text and scope note. A claim is pending for
a paper when the hash is missing (the claim is new) or different (you reworded it). Only
pending claims go in the menu.

This matters because the naive version was silently broken: `claims_done` was a boolean,
so adding a claim left every paper marked finished and the new node got nothing, with the
run cheerfully reporting $0.00.

| you changed | what re-runs |
|---|---|
| added a claim | that claim, against every in-scope paper |
| reworded a claim or its note | that claim, against every in-scope paper |
| added papers in Zotero | all claims, against those papers only |
| edited a claim's evidence list by hand | nothing — human edges are never touched |
| retired an axis in TAXONOMY | labelling only; claim edges survive |

`--reassign C2.3b` still exists for forcing a redo without changing the wording.

## Cost, measured on a 270-paper library

The claim menu and the rules are identical across papers, so they go in a cached system
prefix and only the abstract is billed at full rate. Papers are grouped by pending set so
the prefix stays stable within a group and the cache actually hits.

| | without caching | with caching |
|---|---|---|
| first full pass, 25 claims | $2.39 | **$1.06** |
| add one claim | $0.87 | **$0.50** |
| add five claims in one run | $1.20 | **$0.65** |
| reword one claim | $0.87 | **$0.50** |
| add 30 papers, all claims | $0.41 | **$0.19** |

The number worth remembering: five claims added one at a time costs $2.48; the same five
added in one run costs $0.65. The per-paper floor is the abstract, which gets re-sent
whatever the menu size — so **batch your claim edits.** Write several, then run once.

`assign_claims.py` prints the real billed cost and the cache hit rate at the end, so you
can check this rather than trust the estimate.
