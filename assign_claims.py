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
import argparse, hashlib, json, os, sys, time, collections
import yaml
import build_db as B


def claim_hash(c):
    """Identity of one claim's WORDING. Changing the text or the scope note changes the
    question being asked, so every paper must be re-evaluated against it. Adding a claim
    is just the case where the old hash is absent."""
    body = " ".join((c["text"] + " " + (c.get("note") or "")).split())
    return hashlib.sha1(body.encode()).hexdigest()[:8]


def pending_for(paper, claims):
    """Claims this paper has never been evaluated against, or was evaluated against
    under different wording. This is the unit of work — not the paper."""
    seen = paper.get("claims_seen") or {}
    return [c for c in claims if seen.get(c["id"]) != claim_hash(c)]


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


SYSTEM = """You are attaching papers to a fixed set of scientific claims.

CLAIMS (you may only cite these ids; you may NOT invent a claim):
{menu}

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
- You are shown only the claims that still need evaluating for this paper. Claims absent
  from the list have already been decided; do not mention them.
"""

USER = """PAPER
title: {title}
year: {year}
venue: {venue}
authors: {authors}
labels: {labels}
abstract: {abstract}"""


ANTHROPIC = "https://api.anthropic.com/v1/messages"


def call(paper, menu, key):
    """The claim menu and the rules are identical for every paper in a batch, so they go
    in a cached system prefix. Only the abstract is billed at full rate. Papers are
    grouped by pending set precisely so this prefix stays stable and the cache hits."""
    system = [{"type": "text",
               "text": SYSTEM.format(menu=json.dumps(menu, indent=1)),
               "cache_control": {"type": "ephemeral"}}]
    user = USER.format(
        title=paper["title"], year=paper.get("year"), venue=paper.get("venue", ""),
        authors=", ".join(paper.get("authors", [])[:8]),
        labels=json.dumps(paper.get("labels", {})),
        abstract=(paper.get("abstract") or "")[:5000])
    import requests, re
    r = requests.post(ANTHROPIC,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": B.MODEL, "max_tokens": 900, "system": system,
              "messages": [{"role": "user", "content": user}]}, timeout=90)
    r.raise_for_status()
    body = r.json()
    txt = "".join(b.get("text", "") for b in body.get("content", []))
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    u = body.get("usage", {})
    return json.loads(txt), u


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
        q = [p for p in in_scope if (p.get("claim_edges") is not None
             and not p["claim_edges"] and p.get("claims_seen"))]
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
            for cid in a.reassign:
                (p.get("claims_seen") or {}).pop(cid, None)
        print(f"  dropped {n} model edges for {', '.join(a.reassign)}")

    # Unit of work is a (paper, pending-claim-set) pair, not a paper. Grouping by that
    # set also keeps the cached prefix stable within each group.
    groups = collections.defaultdict(list)
    for p in in_scope:
        if len(p.get("abstract", "")) < 120:
            continue
        pend = pending_for(p, claims)
        if pend:
            groups[tuple(c["id"] for c in pend)].append(p)
    skipped = [p for p in in_scope if len(p.get("abstract", "")) < 120]

    n_todo = sum(len(v) for v in groups.values())
    tok_in = 0
    for ids, ps in groups.items():
        sub = [c for c in claims if c["id"] in set(ids)]
        menu_tok = len(json.dumps(claim_menu(sub))) // 4
        tok_in += 900 + menu_tok                      # cache write, once per group
        tok_in += len(ps) * (min(len(p.get("abstract","")) for p in ps) // 4 + 120)
    cached = sum((900 + len(json.dumps(claim_menu([c for c in claims if c["id"] in set(ids)]))) // 4)
                 * (len(ps) - 1) for ids, ps in groups.items())
    print(f"{len(in_scope)} papers in scope · {n_todo} to evaluate · {len(skipped)} lack abstracts")
    if groups:
        print("  work by pending claim set:")
        for ids, ps in sorted(groups.items(), key=lambda x: -len(x[1]))[:6]:
            label = ", ".join(ids) if len(ids) <= 4 else f"{len(ids)} claims"
            print(f"    {len(ps):4d} papers x  {label}")
    est = tok_in/1e6*3 + cached/1e6*0.30 + n_todo*260/1e6*15
    print(f"  ~{tok_in:,} fresh + {cached:,} cached tokens · roughly ${est:.2f} on Sonnet")
    if a.dry_run:
        print("\n  dry run — nothing sent")
        return

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not set")

    done = 0
    usage = collections.Counter()
    for ids, ps in sorted(groups.items(), key=lambda x: -len(x[1])):
        sub = [c for c in claims if c["id"] in set(ids)]
        menu = claim_menu(sub)
        for p in ps:
            try:
                out, u = call(p, menu, key)
            except Exception as e:
                print(f"  ! {p['title'][:46]}: {e}", file=sys.stderr)
                continue
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
                usage[k] += u.get(k, 0)
            # replace only the edges for claims just evaluated; keep human edges always
            keep = [e for e in (p.get("claim_edges") or [])
                    if e["claim"] not in set(ids) or e.get("source") == "human"]
            fresh = []
            for e in out.get("edges", []):
                if e.get("claim") not in set(ids):
                    continue                    # invented, or not on the menu
                e["source"] = "model"
                e["needs_review"] = float(e.get("confidence", 0)) < 0.6
                fresh.append(e)
            p["claim_edges"] = keep + fresh
            p.setdefault("claims_seen", {}).update({c["id"]: claim_hash(c) for c in sub})
            p.pop("claims_done", None)
            done += 1
            tag = ", ".join(f"{e['claim']}:{e['stance'][:4]}" for e in fresh) or "—"
            print(f"  [{done}/{n_todo}] {tag:30s} {p['title'][:42]}")
            if done % a.every == 0:
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
    if usage:
        billed = (usage["input_tokens"]/1e6*3 + usage["cache_creation_input_tokens"]/1e6*3.75
                  + usage["cache_read_input_tokens"]/1e6*0.30 + usage["output_tokens"]/1e6*15)
        hit = usage["cache_read_input_tokens"]
        tot = hit + usage["cache_creation_input_tokens"] + usage["input_tokens"]
        print(f"\n  actual cost ${billed:.2f} · cache served {hit:,}/{tot:,} input tokens "
              f"({100*hit//max(tot,1)}%)")
    print(f"wrote {a.papers}  ·  {sum(n.values())} edges  ·  {dict(n)}")
    print(f"  {unmatched} in-scope papers matched nothing — run --queue to read them")
    empty = [c["id"] for c in claims if per[c["id"]] == 0]
    if empty:
        print(f"  claims with no paper attached: {', '.join(empty)}")


if __name__ == "__main__":
    main()
