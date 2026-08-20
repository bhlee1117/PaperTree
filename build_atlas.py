#!/usr/bin/env python3
"""
build_atlas.py — merge claims.yaml + papers.json into atlas.json for the viewer.

    python3 build_atlas.py

Two sources of evidence edges, merged here:
  claims.yaml   hand-authored, source: human. The spine. Survives everything.
  papers.json   machine-proposed, source: model, written by assign_claims.py.

When both describe the same paper-claim pair the human edge wins and the model edge is
dropped. Matching is by citation key on the human side and DOI on the machine side, so
claims.yaml carries an optional `doi` per edge; edges without one are matched by key
against a slugified author-year and reported if they fail, rather than silently lost.

Also computes, per claim, the diagnostics that the surviving taxonomy exists to provide:
what preparations the evidence comes from, what modalities, what years, and which
methodological caveats it inherits from Q7.
"""
import json, re, sys, collections
import yaml


def slug(name, year):
    return re.sub(r"[^a-z]", "", (name or "").split()[-1].lower()) + f"_{year}"


def main():
    cdoc = yaml.safe_load(open("claims.yaml"))
    src = sys.argv[1] if len(sys.argv) > 1 else "papers.json"
    db = json.load(open(src))
    papers = db["papers"]
    rules = cdoc["meta"].get("caveat_rules", {})
    if not any((p.get("labels") or {}).get("modality") for p in papers):
        print("  ! no paper carries a `modality` label. Caveat inheritance will be empty.\n"
              "    Re-run ./update.sh — this database was built under an older taxonomy.",
              file=sys.stderr)

    by_doi = {(p.get("doi") or "").lower(): p for p in papers if p.get("doi")}
    by_slug = {}
    for p in papers:
        if p.get("last_author") and p.get("year"):
            by_slug.setdefault(slug(p["last_author"], p["year"]), p)
        if p.get("first_author") and p.get("year"):
            by_slug.setdefault(slug(p["first_author"], p["year"]), p)

    edges, unresolved = [], []
    # 1. human edges from claims.yaml
    for c in cdoc["claims"]:
        for e in c.get("evidence", []):
            p = by_doi.get((e.get("doi") or "").lower()) or by_slug.get(e["key"])
            edges.append({
                "claim": c["id"], "key": e["key"],
                "paper_id": p["id"] if p else None,
                "stance": e["stance"], "strength": e["strength"],
                "region": e.get("region"), "modality": e.get("modality"),
                "flags": e.get("flags", []),
                "condition": e.get("condition", ""), "source": "human",
            })
            if not p:
                unresolved.append((c["id"], e["key"]))

    # 2. model edges from papers.json, unless a human edge already covers the pair
    human_pairs = {(x["claim"], x["paper_id"]) for x in edges if x["paper_id"]}
    dropped = 0
    for p in papers:
        for e in p.get("claim_edges") or []:
            if (e["claim"], p["id"]) in human_pairs:
                dropped += 1
                continue
            mods = {"patch": "patch", "voltage imaging": "voltage",
                    "calcium imaging": "calcium", "modeling": "model",
                    "review": "review"}
            edges.append({
                "claim": e["claim"], "key": p["id"], "paper_id": p["id"],
                "stance": e["stance"], "strength": e["strength"],
                "region": None,
                "modality": mods.get((p.get("labels") or {}).get("modality"), "other"),
                "flags": [f for f in p.get("method_flags", [])
                          if f in ("optogenetics", "anesthetized", "room temperature")],
                "condition": e.get("condition", ""), "quote": e.get("quote", ""),
                "source": e.get("source", "model"),
                "needs_review": e.get("needs_review", False),
            })

    # 3. per-claim diagnostics
    FLAGMAP = {"optogenetics": "optogenetic", "anesthetized": "anesthetized",
               "room temperature": "room_temp"}
    pid = {p["id"]: p for p in papers}
    claims = []
    for c in cdoc["claims"]:
        mine = [e for e in edges if e["claim"] == c["id"]]
        sup = [e for e in mine if e["stance"] in ("supports", "qualifies")]
        cav = collections.Counter()
        for e in sup:
            if e.get("modality") in rules and rules[e["modality"]] != c["id"]:
                cav[rules[e["modality"]]] += 1
            for f in e.get("flags", []):
                k = FLAGMAP.get(f, f)
                if k in rules and rules[k] != c["id"]:
                    cav[rules[k]] += 1
        yrs = [pid[e["paper_id"]].get("year") for e in mine
               if e["paper_id"] and pid[e["paper_id"]].get("year")]
        preps = collections.Counter(
            (pid[e["paper_id"]].get("labels") or {}).get("prep")
            for e in sup if e["paper_id"])
        claims.append({**c,
            "n": collections.Counter(e["stance"] for e in mine),
            "modalities": dict(collections.Counter(e.get("modality") for e in sup)),
            "preps": {k: v for k, v in preps.items() if k},
            "years": [min(yrs), max(yrs)] if yrs else None,
            "inherits": dict(cav),
            "direct_sources": len({e["key"] for e in sup if e["strength"] == "direct"}),
        })

    out = {"meta": {**db.get("meta", {}), **{k: v for k, v in cdoc["meta"].items()}},
           "questions": cdoc["questions"], "claims": claims,
           "edges": edges, "papers": papers}
    json.dump(out, open("atlas.json", "w"), ensure_ascii=False, indent=1)

    n = collections.Counter(e["source"] for e in edges)
    print(f"wrote atlas.json · {len(claims)} claims · {len(edges)} edges {dict(n)}")
    print(f"  {dropped} model edges superseded by a human edge")
    if unresolved:
        print(f"  ! {len(unresolved)} human edges cite a paper not in the library:")
        for cid, k in unresolved[:12]:
            print(f"      {cid}  {k}")
        print("    add the DOI to claims.yaml, or add the paper to Zotero")


if __name__ == "__main__":
    main()
