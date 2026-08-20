#!/usr/bin/env python3
"""
assign_claims.py — attach papers to claims in claims.yaml.

    python3 assign_claims.py                        assign the unassigned
    python3 assign_claims.py --dry-run              cost and coverage only
    python3 assign_claims.py --reassign C2.3b       redo one claim after editing it
    python3 assign_claims.py --queue                show papers that matched nothing

SEPARATE FROM build_db.py ON PURPOSE
claims.yaml will change far more often than the taxonomy does — every time you split a
node, sharpen a claim's wording, or add one. Re-running claim assignment must not mean
re-running (and re-paying for) the labelling stage. So labels live in build_db.py's
output and claim edges are layered on top here.

WHAT THE MODEL IS AND IS NOT ALLOWED TO DO
It picks from the existing claim ids. It cannot invent a claim. Papers that match none
land in the review queue, and a growing queue is the signal that YOU need to write a new
claim node — that judgement is not delegated.

PROVENANCE
Machine-proposed edges carry source: model. Edges you confirm or write in the viewer
carry source: human and are never overwritten, exactly like human_verified on labels.
Hand-authored edges already in claims.yaml stay there and are merged at build time; this
script only ever writes into papers.json.
"""
import argparse, json, os, sys, time, collections
import yaml
import build_db as B


def load_claims(path="claims.yaml"):
    d = yaml.safe_load(open(path))
    claims = [c for c in d["claims"]]
    return d, claims


def claim_menu(claims):
    """The closed set handed to the model. Deliberately includes `note`, truncated:
    the notes carry the scope distinctions that keep near-miss papers off a node."""
    out = []
    for c in claims:
        note = " ".join((c.get("note") or "").split())
        out.append({
            "id": c["id"],
            "claim": " ".join(c["text"].split()),
            "scope_note": note[:240],
        })
    return out


PROMPT = """You are attaching a paper to a fixed set of scientific claims.

CLAIMS (you may only cite these ids; you may NOT invent a claim):
{menu}

PAPER
title: {title}
year: {year}
venue: {venue}
authors: {authors}
labels: {labels}
abstract: {abstract}

For each claim this paper genuinely bears on, emit one edge. Most papers bear on 0-3
claims. Returning an empty list is a perfectly good answer and is much better than a
stretched match.

Return ONLY JSON, no prose, no fences:
{{
  "edges": [
    {{"claim": "C1.1",
      "stance": "supports|qualifies|contradicts|assumes",
      "strength": "direct|indirect|assumed",
      "condition": "<=25 words: the scope under which this paper's result holds",
      "quote": "<=12 words from the abstract that justify this edge",
      "confidence": 0.0-1.0}}
  ]
}}

STANCE
  supports     the paper's own result argues the claim is true
  qualifies    true only under a condition the paper identifies, or the paper narrows it
  contradicts  the paper's result argues the claim is false
  assumes      the paper takes the claim as given and builds on it WITHOUT testing it

STRENGTH
  direct    testing this claim was a purpose of the experiment
  indirect  the data bear on it, but it was not what the paper set out to test
  assumed   no data bear on it here; use with stance=assumes

RULES
- `assumes` is the most common relation in a mature field and the one people forget.
  If the paper cites the claim as background and moves on, that is assumes/assumed,
  NOT supports. Getting this wrong makes weak claims look strong.
- Read scope_note. It usually says what does NOT belong on the node.
- A review paper mostly produces `assumes` edges plus occasional `qualifies`. It rarely
  produces `supports/direct`, because it has no data of its own.
- confidence below 0.6 flags the edge for human review. Be honest.
"""


def call(paper, menu, key, lang="English"):
    p = PROMPT.format(
        menu=json.dumps(menu, indent=1),
        title=paper["title"], year=paper.get("year"), venue=paper.get("venue", ""),
        authors=", ".join(paper.get("authors", [])[:8]),
        labels=json.dumps(paper.get("labels", {})),
        abstract=(paper.get("abstract") or "")[:5000],
    )
    return B.call_claude(p, key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", default="papers.json")
    ap.add_argument("--claims", default="claims.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reassign", nargs="*", default=[], metavar="CLAIM_ID",
                    help="drop model edges for these claims and redo them")
    ap.add_argument("--queue", action="store_true", help="list unmatched papers and stop")
    ap.add_argument("--scope", default="dendritic computation,hippocampal coding & memory",
                    help="comma-separated scopes to consider; '*' for all")
    ap.add_argument("--every", type=int, default=20, help="checkpoint interval")
    a = ap.parse_args()

    db = json.load(open(a.papers))
    papers = db["papers"]
    cdoc, claims = load_claims(a.claims)
    valid = {c["id"] for c in claims}
    menu = claim_menu(claims)

    scopes = None if a.scope == "*" else set(s.strip() for s in a.scope.split(","))
    in_scope = [p for p in papers
                if scopes is None or (p.get("labels") or {}).get("scope") in scopes]

    if a.queue:
        q = [p for p in in_scope if p.get("claim_edges") == []]
        print(f"{len(q)} in-scope papers matched no claim.\n"
              "A growing queue means the tree is missing a node — read a few and write one.\n")
        for p in sorted(q, key=lambda x: -(x.get("cited_by") or 0))[:40]:
            print(f"  {p.get('year')}  {(p.get('cited_by') or 0):>6}  {p['title'][:74]}")
        return

    # drop model-authored edges for claims being reassigned; keep human ones
    if a.reassign:
        bad = set(a.reassign) - valid
        if bad:
            sys.exit(f"unknown claim ids: {sorted(bad)}")
        n = 0
        for p in papers:
            keep = [e for e in (p.get("claim_edges") or [])
                    if e["claim"] not in set(a.reassign) or e.get("source") == "human"]
            n += len(p.get("claim_edges") or []) - len(keep)
            if p.get("claim_edges") is not None:
                p["claim_edges"] = keep
                p.pop("claims_done", None)
        print(f"  dropped {n} model edges for {', '.join(a.reassign)}")

    todo = [p for p in in_scope
            if not p.get("claims_done") and len(p.get("abstract", "")) >= 120]
    skipped = [p for p in in_scope if len(p.get("abstract", "")) < 120]
    tok = sum(1500 + len(json.dumps(menu)) // 4 + min(len(p.get("abstract", "")), 5000) // 4
              for p in todo)
    print(f"{len(in_scope)} papers in scope · {len(todo)} to assign · {len(skipped)} lack abstracts")
    print(f"  ~{tok:,} input tokens · roughly ${tok/1e6*3 + len(todo)*500/1e6*15:.2f} on Sonnet")
    if a.dry_run:
        print("\n  dry run — nothing sent")
        return

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not set")

    for i, p in enumerate(todo, 1):
        try:
            out = call(p, menu, key)
        except Exception as e:
            print(f"  ! {p['title'][:46]}: {e}", file=sys.stderr)
            continue
        edges = []
        for e in out.get("edges", []):
            if e.get("claim") not in valid:
                continue                       # silently drop invented claims
            e["source"] = "model"
            e["needs_review"] = float(e.get("confidence", 0)) < 0.6
            edges.append(e)
        p["claim_edges"] = (p.get("claim_edges") or []) + edges
        p["claims_done"] = True
        tag = ", ".join(f"{e['claim']}:{e['stance'][:4]}" for e in edges) or "—"
        print(f"  [{i}/{len(todo)}] {tag:28s} {p['title'][:44]}")
        if i % a.every == 0:
            json.dump(db, open(a.papers, "w"), ensure_ascii=False, indent=1)
        time.sleep(0.2)

    for p in skipped:
        p.setdefault("claim_edges", [])
    json.dump(db, open(a.papers, "w"), ensure_ascii=False, indent=1)

    n = collections.Counter()
    per = collections.Counter()
    for p in papers:
        for e in p.get("claim_edges") or []:
            n[e["stance"]] += 1
            per[e["claim"]] += 1
    unmatched = sum(1 for p in in_scope if p.get("claim_edges") == [])
    print(f"\nwrote {a.papers}  ·  {sum(n.values())} edges  ·  {dict(n)}")
    print(f"  {unmatched} in-scope papers matched nothing — run --queue to read them")
    empty = [c["id"] for c in claims if per[c["id"]] == 0]
    if empty:
        print(f"  claims with no paper attached: {', '.join(empty)}")


if __name__ == "__main__":
    main()
