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
